"""
Stripe Checkout Blueprint for Fitness Dashboard
Handles $9/mo Pro subscription via Stripe Checkout hosted page.
"""

import os
import stripe
from flask import Blueprint, redirect, render_template, request, url_for, flash, current_app
from flask_login import login_required, current_user

stripe_bp = Blueprint('stripe_bp', __name__)

STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')


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

    event_type = event.get('type', '')

    if event_type == 'checkout.session.completed':
        session_obj = event['data']['object']
        user_id = session_obj.get('metadata', {}).get('user_id')
        stripe_customer = session_obj.get('customer')
        stripe_sub = session_obj.get('subscription')
        _mark_user_pro(user_id, stripe_customer=stripe_customer, stripe_sub=stripe_sub)

    elif event_type in ('customer.subscription.deleted', 'customer.subscription.paused'):
        sub = event['data']['object']
        _revoke_user_pro(sub.get('id'))

    elif event_type == 'invoice.payment_failed':
        # Optional: log but don't revoke immediately — let Stripe retry first
        sub_id = event['data']['object'].get('subscription')
        current_app.logger.warning(f'Payment failed for subscription {sub_id}')

    return '', 200


def _mark_user_pro(user_id, stripe_customer=None, stripe_sub=None):
    """Mark the user as a Pro subscriber in the database."""
    if not user_id:
        return
    try:
        from auth import User
        User.mark_pro(int(user_id), stripe_customer=stripe_customer, stripe_sub=stripe_sub)
        current_app.logger.info(f'User {user_id} upgraded to Pro (customer={stripe_customer})')
    except Exception as e:
        current_app.logger.error(f'Could not mark user {user_id} as pro: {e}')


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
    except Exception as e:
        current_app.logger.error(f'Could not revoke pro for sub {stripe_sub_id}: {e}')
