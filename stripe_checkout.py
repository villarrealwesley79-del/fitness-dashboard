"""
Stripe Checkout Blueprint for Fitness Dashboard
Handles $9/mo Pro subscription via Stripe Checkout hosted page.

DORMANT (FIT-299): This blueprint is intentionally unregistered. The `stripe`
package is not in requirements.txt, so registering it would fail at import time.
"""

import os
import sqlite3
from datetime import datetime, timezone
import stripe
from flask import Blueprint, redirect, render_template, request, url_for, flash, current_app
from flask_login import login_required, current_user
from runtime_config import data_path

stripe_bp = Blueprint('stripe_bp', __name__)

STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _stripe_event_db():
    return data_path('auth.db')


def _init_stripe_event_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stripe_webhook_events (
            event_id        TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            event_created_at INTEGER,
            status          TEXT NOT NULL,
            received_at     TEXT NOT NULL,
            processed_at    TEXT
        )
        """
    )


def _process_event_once(event, side_effect):
    """Run one verified Stripe event once and retain metadata-only audit state."""
    event_id = event.get('id')
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError('Stripe event id is required')

    conn = sqlite3.connect(_stripe_event_db(), timeout=5)
    try:
        _init_stripe_event_db(conn)
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            'SELECT status FROM stripe_webhook_events WHERE event_id = ?',
            (event_id,),
        ).fetchone()
        if existing and existing[0] == 'processed':
            conn.commit()
            return False

        received_at = _utc_now()
        conn.execute(
            """
            INSERT INTO stripe_webhook_events (
                event_id, event_type, event_created_at, status, received_at, processed_at
            ) VALUES (?, ?, ?, 'processing', ?, NULL)
            ON CONFLICT(event_id) DO UPDATE SET
                event_type=excluded.event_type,
                event_created_at=excluded.event_created_at,
                status='processing',
                received_at=excluded.received_at,
                processed_at=NULL
            """,
            (event_id, event.get('type', ''), event.get('created'), received_at),
        )
        try:
            side_effect(conn)
        except Exception:
            conn.rollback()
            _record_failed_event(event)
            raise
        conn.execute(
            """
            UPDATE stripe_webhook_events
            SET status='processed', processed_at=?
            WHERE event_id=?
            """,
            (_utc_now(), event_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _record_failed_event(event):
    with sqlite3.connect(_stripe_event_db(), timeout=5) as conn:
        _init_stripe_event_db(conn)
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO stripe_webhook_events (
                event_id, event_type, event_created_at, status, received_at, processed_at
            ) VALUES (?, ?, ?, 'failed', ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                event_type=excluded.event_type,
                event_created_at=excluded.event_created_at,
                status='failed',
                processed_at=excluded.processed_at
            """,
            (event['id'], event.get('type', ''), event.get('created'), now, now),
        )


def get_stripe():
    """Return configured stripe module or None if not configured."""
    key = os.environ.get('STRIPE_SECRET_KEY', '')
    if not key:
        return None
    stripe.api_key = key
    return stripe


@stripe_bp.route('/pricing')
def pricing():
    return render_template('pricing.html')


@stripe_bp.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    s = get_stripe()
    if not s:
        flash('Stripe is not configured. Contact support.', 'error')
        return redirect(url_for('stripe_bp.pricing'))

    price_id = os.environ.get('STRIPE_PRICE_ID', '')
    if not price_id:
        flash('Stripe price not configured. Contact support.', 'error')
        return redirect(url_for('stripe_bp.pricing'))

    try:
        session = s.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=request.host_url.rstrip('/') + url_for('stripe_bp.success') + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.host_url.rstrip('/') + url_for('stripe_bp.cancel'),
            customer_email=getattr(current_user, 'email', None),
            metadata={'user_id': str(current_user.get_id())},
        )
        return redirect(session.url, code=303)
    except Exception as e:
        flash(f'Could not start checkout: {e}', 'error')
        return redirect(url_for('stripe_bp.pricing'))


@stripe_bp.route('/success')
def success():
    return render_template('checkout_success.html')


@stripe_bp.route('/cancel')
def cancel():
    return render_template('checkout_cancel.html')


@stripe_bp.route('/webhook', methods=['POST'])
def webhook():
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    payload = request.get_data(as_text=False)
    sig_header = request.headers.get('Stripe-Signature', '')

    s = get_stripe()
    if not s:
        return 'Stripe not configured', 400
    if not webhook_secret:
        current_app.logger.error('Stripe webhook secret is not configured; refusing unsigned webhook')
        return 'Stripe webhook secret is not configured', 503

    try:
        event = s.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.warning(f'Stripe webhook error: {e}')
        return 'Invalid payload or signature', 400

    try:
        _process_event_once(event, lambda conn: _apply_stripe_event(event, conn))
    except ValueError:
        return 'Invalid Stripe event', 400
    except Exception:
        current_app.logger.exception('Stripe webhook processing failed')
        return 'Stripe webhook processing failed', 500

    return '', 200


def _apply_stripe_event(event, conn):
    event_type = event.get('type', '')

    if event_type == 'checkout.session.completed':
        session_obj = event['data']['object']
        user_id = session_obj.get('metadata', {}).get('user_id')
        stripe_customer = session_obj.get('customer')
        stripe_sub = session_obj.get('subscription')
        if user_id:
            conn.execute(
                "UPDATE users SET is_pro=1, stripe_customer=?, stripe_sub=? WHERE id=?",
                (stripe_customer, stripe_sub, int(user_id)),
            )
            current_app.logger.info('Stripe checkout entitlement applied')

    elif event_type in ('customer.subscription.deleted', 'customer.subscription.paused'):
        sub = event['data']['object']
        stripe_sub_id = sub.get('id')
        if stripe_sub_id:
            conn.execute(
                "UPDATE users SET is_pro=0, stripe_sub=NULL WHERE stripe_sub=?",
                (stripe_sub_id,),
            )
            current_app.logger.info('Stripe subscription entitlement revoked')

    elif event_type == 'invoice.payment_failed':
        # Optional: log but don't revoke immediately — let Stripe retry first
        sub_id = event['data']['object'].get('subscription')
        current_app.logger.warning(f'Payment failed for subscription {sub_id}')


def _mark_user_pro(user_id, stripe_customer=None, stripe_sub=None):
    """Mark the user as a Pro subscriber in the database."""
    if not user_id:
        return
    try:
        from auth import User
        User.mark_pro(int(user_id), stripe_customer=stripe_customer, stripe_sub=stripe_sub)
        current_app.logger.info(f'User {user_id} upgraded to Pro (customer={stripe_customer})')
    except Exception:
        current_app.logger.exception('Could not update Pro entitlement')
        raise


def _revoke_user_pro(stripe_sub_id):
    """Revoke Pro for a user identified by their Stripe subscription ID."""
    if not stripe_sub_id:
        return
    try:
        import sqlite3
        from auth import AUTH_DB, User
        conn = sqlite3.connect(AUTH_DB)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT id FROM users WHERE stripe_sub=?", (stripe_sub_id,)).fetchone()
        finally:
            conn.close()
        if row:
            User.revoke_pro(row["id"])
            current_app.logger.info(f'Pro revoked for user {row["id"]} (sub={stripe_sub_id})')
    except Exception:
        current_app.logger.exception('Could not revoke Pro entitlement')
        raise
