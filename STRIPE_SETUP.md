# Stripe Checkout Setup Guide
*FitOS Fitness Dashboard — $9/mo Pro Subscriptions*

## What's Done (Mar 15, 2026)

The Stripe Checkout integration is fully wired in. Here's what was built:

### Files Changed
- `stripe_checkout.py` — Blueprint with `/pricing`, `/create-checkout-session`, `/success`, `/cancel`, `/webhook`
- `auth.py` — User model extended with `email`, `is_pro`, `stripe_customer`, `stripe_sub` fields + `mark_pro()` / `revoke_pro()` static methods
- `auth.db` — Auto-migrated with new columns (existing users preserved)
- `app.py` — `stripe_bp` blueprint registered
- `requirements.txt` — `stripe>=14.0.0` added
- `templates/login.html` — Optional email field on register form
- `templates/pricing.html` — Pricing page (Free vs Pro $9/mo)
- `templates/checkout_success.html` — Post-payment confirmation
- `templates/checkout_cancel.html` — Cancelled checkout fallback

### Webhook Events Handled
| Event | Action |
|-------|--------|
| `checkout.session.completed` | Sets `is_pro=1`, saves `stripe_customer`+`stripe_sub` |
| `customer.subscription.deleted` | Sets `is_pro=0`, clears `stripe_sub` |
| `customer.subscription.paused` | Sets `is_pro=0`, clears `stripe_sub` |
| `invoice.payment_failed` | Logs warning (Stripe retries, no immediate revoke) |

---

## To Go Live — 3 Steps

### Step 1: Create Stripe Account + Product
1. Go to https://dashboard.stripe.com → Products → Add Product
2. Name: "FitOS Pro"
3. Price: $9.00/month (recurring)
4. Copy the **Price ID** (starts with `price_`)

### Step 2: Set Environment Variables
Add to your `.env` file in `/projects/fitness-dashboard/`:
```
STRIPE_SECRET_KEY=sk_live_...       # from Stripe Dashboard → API Keys
STRIPE_PRICE_ID=price_...           # from Step 1
STRIPE_WEBHOOK_SECRET=whsec_...     # from Step 3
```

For local testing, use `sk_test_...` keys instead.

### Step 3: Set Up Webhook
1. Stripe Dashboard → Developers → Webhooks → Add Endpoint
2. URL: `https://your-domain.com/webhook` (or ngrok URL for testing)
3. Events to listen for:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `customer.subscription.paused`
   - `invoice.payment_failed`
4. Copy the **Webhook Secret** (starts with `whsec_`)

---

## Testing Locally with Stripe CLI
```bash
# Install Stripe CLI (Homebrew)
brew install stripe/stripe-cli/stripe

# Login
stripe login

# Forward webhooks to local server
stripe listen --forward-to localhost:5050/webhook

# Trigger a test payment
stripe trigger checkout.session.completed
```

---

## Access Control (Future)
Currently `is_pro` is set but not enforced in routes.
Next sprint: add `@pro_required` decorator that checks `current_user.is_pro`.

Example:
```python
from functools import wraps
from flask import redirect, url_for
from flask_login import current_user

def pro_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_pro:
            return redirect(url_for('stripe_bp.pricing'))
        return f(*args, **kwargs)
    return decorated
```

---

## Flow Summary
```
User → /pricing → clicks "Upgrade to Pro"
     → POST /create-checkout-session
     → Redirected to Stripe Hosted Checkout
     → Pays → Stripe fires webhook to /webhook
     → _mark_user_pro() sets is_pro=1 in auth.db
     → User redirected to /success
```
