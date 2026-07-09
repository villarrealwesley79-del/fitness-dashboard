# Billing, Stripe & Landing — PRD

> **Sources:** `stripe_checkout.py`, `templates/landing.html`, `templates/pricing.html`, `templates/checkout_success.html`, `templates/checkout_cancel.html`, `auth.py`, `app.py`, `tests/test_csrf_protection.py`, `tests/test_fit183_runtime_paths.py`, `tests/test_whoop_ui_contract.py`, `README.md`, `docs/CURRENT_STATE.md`
> **Routes:** Blueprint contract: `/pricing`, `/create-checkout-session`, `/success`, `/cancel`, `/webhook`. Public allowlist also includes `/landing`. `stripe_bp` is defined but never registered (only `auth_bp` is registered in `auth.py`), so these blueprint routes 404 in the running app; `/landing` has no route.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Billing, Stripe & Landing is a partially implemented SaaS/productization layer around a mostly single-owner local-first Fitness Dashboard. The codebase contains public marketing and pricing templates, a Stripe Checkout blueprint, and `users` table fields for Pro subscription state. The actual app remains owner-first: current product docs describe the app as primarily for one owner, local runtime data is not multi-tenant, and most product surfaces are auth-gated personal dashboard flows.

The intended billing scenario is: a visitor sees FitOS marketing, registers a local account, opens pricing, submits a CSRF-protected checkout form, is redirected to Stripe Checkout for a `$9/mo` subscription, and Stripe calls the webhook to mark that local user as Pro. If Stripe later sends subscription deleted or paused, the app revokes Pro for the row matching the Stripe subscription ID.

The current reachable runtime is less complete. `stripe_checkout.py` defines a Flask blueprint, but the app never registers `stripe_bp`; only `auth_bp` is registered in the inspected Flask wiring. Similarly, `templates/landing.html` exists and `/landing` is public in auth, but no `/landing` route exists. This PRD documents both the implemented contract and the confirmed dead reachability.

No current app feature gate was found that checks `current_user.is_pro` or `User.is_pro` before allowing Oura, smart recommendations, unlimited history, multi-user access, or priority features. The billing state is persisted, but the subscription is not currently a product access boundary in the inspected code.

## 2. User-Facing Surfaces

| Surface | File | Current reachability | Behavior |
| --- | --- | --- | --- |
| Landing page | `templates/landing.html` | Template exists; no `/landing` route. | Public marketing page for "FitOS — Evidence-Based Training Intelligence" with navigation, hero, social proof, stats, mock dashboard, feature cards, how-it-works, testimonials, pricing, FAQ, CTA, and footer. |
| Pricing page | `templates/pricing.html` through `stripe_checkout.pricing` | Blueprint defined in `stripe_checkout.py` but never registered; route 404. | Public simple pricing page with Free and Pro cards. Pro card submits a CSRF-protected checkout form. |
| Checkout success | `templates/checkout_success.html` through `/success` | Blueprint defined in `stripe_checkout.py` but never registered; route 404. | Public confirmation page saying Pro account is active and links to dashboard. Does not verify `session_id`. |
| Checkout cancel | `templates/checkout_cancel.html` through `/cancel` | Blueprint defined in `stripe_checkout.py` but never registered; route 404. | Public cancellation page saying free account remains active and links back to pricing. |
| Stripe webhook | `stripe_checkout.webhook` at `/webhook` | Blueprint defined in `stripe_checkout.py` but never registered; route 404. | External POST endpoint; auth-public and CSRF-exempt by path. Processes selected Stripe event types. |

The landing page content is more SaaS/market-facing than the rest of the app. It claims a seven-day free trial, Oura integration, recovery-based recommendations, HRV/sleep scoring, unlimited history, nutrition logging, cardio/weather integration, mobile PWA, testimonials, exportability, TLS, encrypted Oura token storage, and no data selling. Several of these are real app capabilities, but the trial/subscription gates and public SaaS posture are not enforced by inspected code.

## 3. Field Inventory

### Landing Page Regions

| Region | Field/Text | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- | --- |
| SEO metadata | `title`, `description`, OG/Twitter tags, canonical URL | Static HTML | Yes | `https://fitos.app/` values | None | Public search/social identity for FitOS. |
| JSON-LD | `WebSite`, `Organization`, `SoftwareApplication`, `offers`, `featureList` | Static structured data | Yes | Free and Pro offers | None | Search-engine product schema. |
| Navigation | Features, Pricing, FAQ, Log In, Start Free Trial | Links | Yes | Anchor and auth links | None | Public page navigation. |
| Hero | Badge, headline, supporting text, CTAs | Static content | Yes | Oura-focused training copy | None | Primary marketing promise. |
| Social proof | Avatar initials and claim | Static content | No | Four initials | None | Credibility claim; not backed by data in repo. |
| Stats band | `87%`, `$9`, `7-day`, `Oura` | Static content | No | Fixed claims | None | Marketing metrics/plan summary. |
| Mock dashboard | Recovery, HRV, readiness, volume, weight, sleep | Static mock values | No | Fixed sample values | None | Illustrative preview, not live data. |
| Feature cards | Oura, programming, body composition, weather, nutrition, PWA | Static cards | Yes | Fixed copy | None | Capability positioning. |
| Pricing section | Free Trial and Pro cards | Static cards | Yes | `$0 / 7 days`, `$9 / month` | None | Plan comparison. |
| FAQ | Six `<details>` items | Static expandable HTML | Yes | Fixed answers | Browser-native details state | Objection handling. |

### Pricing Page Fields

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| Free plan price | Static text | Yes | `$0 / mo` | None | Free tier presentation. |
| Free plan features | List | Yes | Dashboard, Oura Ring sync, workout logging, dim unlimited history, dim multi-user/SaaS, dim support | None | Public plan comparison. |
| Free CTA | Link | Yes | `/register` | None | Account creation path. |
| Pro plan price | Static text | Yes | `$9 / mo` | None | Paid subscription price shown to user. |
| Pro plan features | List | Yes | Everything in Free, unlimited history, SaaS multi-user access, priority support, early access | None | Paid plan promise; not currently enforced. |
| Checkout form `csrf_token` | Hidden string | Yes | `{{ csrf_token }}` | Global CSRF accepts the form token, same-origin browser headers, or XHR header signals; cross-origin browser posts are rejected. | Allows server-rendered POST to checkout. |

### Stripe Checkout Session Request

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `STRIPE_SECRET_KEY` | Env string | Yes for checkout | Empty | `get_stripe()` returns `None` when absent | Enables Stripe API client. |
| `STRIPE_PRICE_ID` | Env string | Yes for checkout | Empty | Missing value redirects to pricing with flash error | Stripe recurring price to sell. |
| `payment_method_types` | List | Yes | `["card"]` | Stripe validates | Card-only checkout. |
| `mode` | String enum | Yes | `subscription` | Stripe validates | Creates subscription instead of one-time payment. |
| `line_items` | List | Yes | One item, quantity `1`, env price ID | Stripe validates | Purchased plan. |
| `success_url` | URL | Yes | `<host>/success?session_id={CHECKOUT_SESSION_ID}` | Built from request host | Return page after successful checkout. |
| `cancel_url` | URL | Yes | `<host>/cancel` | Built from request host | Return page after cancellation. |
| `customer_email` | String or null | No | `current_user.email` | No local validation | Prefills Stripe customer email. |
| `metadata.user_id` | String | Yes | Current Flask-Login user ID | Requires login | Links completed session back to local user row. |

### Stripe Webhook Payload Fields

| Event | Consumed fields | Required | Behavior |
| --- | --- | --- | --- |
| `checkout.session.completed` | `data.object.metadata.user_id`, `customer`, `subscription` | `user_id` required for marking; customer/sub optional | Calls `User.mark_pro(user_id, stripe_customer, stripe_sub)`. |
| `customer.subscription.deleted` | `data.object.id` | Subscription ID required for lookup | Finds user by `stripe_sub`, calls `User.revoke_pro(id)`. |
| `customer.subscription.paused` | `data.object.id` | Subscription ID required for lookup | Same as deleted. |
| `invoice.payment_failed` | `data.object.subscription` | No | Logs warning only; does not revoke. |
| Any other event | `type` | No | Ignored with HTTP 200. |

## 4. Interactions & Flows

### Public Landing Browse

Trigger → Visitor opens intended `/landing` route or a future root marketing route.  
Behavior → Renders the complete static marketing page with anchor navigation and links to `/login`, `/register`, and `#pricing`.  
Validation → None in template.  
API → No `/landing` route exists in the current app; auth allowlist includes `/landing`.  
Success → User can read marketing content and start registration.  
Failure → If no route is registered, request returns 404 despite template/public allowlist.

### Public Pricing Browse

Trigger → Visitor opens intended `/pricing`.  
Behavior → Renders plan cards and checkout form. Flash errors appear above cards.  
Validation → None for GET.  
API → `GET /pricing` in `stripe_checkout.py`.  
Success → User can click Start Free Trial or submit Pro upgrade.  
Failure → Blueprint is not registered, so the route is unreachable in the running app.

### Start Checkout

Trigger → Authenticated user submits Pro checkout form.  
Behavior → Global CSRF guard accepts the form token or same-origin/XHR header signals; cross-origin browser posts are rejected. Route then checks Stripe client and price config. On success, it creates a Stripe hosted Checkout Session and returns a 303 redirect to `session.url`.  
Validation → Requires login. Requires `STRIPE_SECRET_KEY`. Requires `STRIPE_PRICE_ID`. Stripe validates price/payment configuration.  
API → `POST /create-checkout-session`.  
Success → Browser leaves the app for Stripe Checkout.  
Failure → Missing Stripe config redirects back to pricing with flash. Stripe API exception is flashed back to pricing.

### Checkout Success Page

Trigger → Stripe redirects to `/success?session_id=<id>`.  
Behavior → Renders success page and "Go to Dashboard" link.  
Validation → Does not retrieve or verify the `session_id`. Does not itself mark Pro.  
API → `GET /success`.  
Success → User sees success confirmation.  
Failure → If webhook has not fired or blueprint is unreachable, local `is_pro` may not match the page copy.

### Checkout Cancel Page

Trigger → Stripe redirects to `/cancel`.  
Behavior → Renders cancellation page.  
Validation → None.  
API → `GET /cancel`.  
Success → User sees that free account remains active and can return to pricing.

### Webhook Subscription Activation

Trigger → Stripe posts `checkout.session.completed` to `/webhook`.  
Behavior → If `STRIPE_WEBHOOK_SECRET` is set, verifies signature with `stripe.Webhook.construct_event`. If not set, parses raw JSON without signature verification. Extracts local `metadata.user_id`, customer ID, and subscription ID, then updates `users.is_pro=1`, `stripe_customer`, and `stripe_sub`.  
Validation → Requires `STRIPE_SECRET_KEY` because `get_stripe()` must return a configured Stripe module. Signature verification is optional based on env.  
API → `POST /webhook`.  
Success → HTTP 200 empty body. Local user row is upgraded if the row exists and helper does not error.  
Failure → Missing Stripe config returns HTTP 400. Invalid payload/signature returns HTTP 400. Helper exceptions are logged but do not change the HTTP 200 response.

### Webhook Subscription Revocation

Trigger → Stripe posts `customer.subscription.deleted` or `customer.subscription.paused`.  
Behavior → Looks up `users.id` where `stripe_sub` equals the Stripe subscription ID. If found, sets `is_pro=0` and `stripe_sub=NULL`; `stripe_customer` is left unchanged.  
Validation → Same webhook parsing rules as activation.  
API → `POST /webhook`.  
Success → HTTP 200 empty body whether or not a row was found.  
Failure → Helper exceptions are logged, not surfaced to Stripe.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/landing` | Public allowlist | Marketing page | None | HTML landing page | Dead route: template exists, no route found |
| GET | `/pricing` | Public allowlist | Pricing page | None | HTML pricing page | Blueprint defined in `stripe_checkout.py` but never registered; route 404 |
| POST | `/create-checkout-session` | Login required + CSRF | Upgrade button | Form `csrf_token`; env Stripe keys | 303 to Stripe or 302 back to pricing with flash | Blueprint defined in `stripe_checkout.py` but never registered; route 404 |
| GET | `/success` | Public allowlist | Stripe return | Optional `session_id` ignored | HTML success page | Blueprint defined in `stripe_checkout.py` but never registered; route 404 |
| GET | `/cancel` | Public allowlist | Stripe return | None | HTML cancel page | Blueprint defined in `stripe_checkout.py` but never registered; route 404 |
| POST | `/webhook` | Public, CSRF-exempt | Stripe event delivery | Raw body, `Stripe-Signature` header | Empty 200 or text 400 | Blueprint defined in `stripe_checkout.py` but never registered; route 404 |

Endpoint details:

- `/create-checkout-session` returns HTTP 303 only on successful session creation.
- `/webhook` returns HTTP 200 for ignored events, activation/revocation helper failures, and payment-failed logs.
- `/webhook` accepts unsigned JSON when `STRIPE_WEBHOOK_SECRET` is empty, which is unsafe for production.
- `/success` page copy says Pro is active before verifying local entitlement state.

## 6. Data Model & Persistence

Billing state is stored in the `users` table in `auth.db`:

- `email`: optional account email, passed to Stripe Checkout as `customer_email`.
- `is_pro`: integer boolean. `0` means free or not active; `1` means local Pro flag.
- `stripe_customer`: Stripe customer ID from checkout completion.
- `stripe_sub`: Stripe subscription ID from checkout completion; used as revocation lookup key.

`User.mark_pro(user_id, stripe_customer, stripe_sub)` updates all three Pro-related fields except email. `User.revoke_pro(user_id)` sets `is_pro=0` and clears only `stripe_sub`; it does not clear `stripe_customer`.

There is no separate subscriptions table, no webhook event log, no idempotency ledger, no checkout session store, and no trial-expiration store in the inspected code.

## 7. Enums & Constants

| Name | Value(s) | Meaning |
| --- | --- | --- |
| Stripe mode | `subscription` | Checkout creates a recurring subscription. |
| Payment methods | `card` | Only card payments are requested. |
| Visible Pro price | `$9 / mo` and JSON-LD `$9.00` | Public plan price shown in landing/pricing templates. |
| Visible trial | `7-day free trial`, no card needed | Marketing copy only; no trial state enforcement found. |
| Webhook activation event | `checkout.session.completed` | Marks user Pro. |
| Webhook revocation events | `customer.subscription.deleted`, `customer.subscription.paused` | Revokes user Pro by subscription ID. |
| Webhook payment warning | `invoice.payment_failed` | Logs, does not revoke. |
| Stripe env vars | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` | API client, webhook signature secret, and recurring price ID. |
| Local subscription fields | `is_pro`, `stripe_customer`, `stripe_sub` | Entitlement and Stripe linkage on user row. |

## 8. Integration Points

Billing reads account email and user ID from the auth system. Stripe webhook writes back to the same auth database. The public allowlist in auth makes checkout-adjacent pages and `/webhook` reachable without login once their routes exist.

The landing/pricing promises reference other product areas: Oura, recommendation engine, workout logging, nutrition, cardio/weather, PWA, export/import, and data security. Those features are implemented elsewhere, but the paid/free division is not currently wired into those feature routes.

Default single-user registration also blocks the documented funnel once any user exists: `/register` returns 403 unless `FITNESS_DASHBOARD_SINGLE_USER=false`. The owner-only global guard is stronger than missing Pro gates: authenticated non-owner users are rejected with 403 before reaching the dashboard, so a paying non-owner account could not use the product in the default model.

Operations/deployment matter for billing because webhook verification depends on `STRIPE_WEBHOOK_SECRET` and public reachability depends on a host URL that Stripe can call. See [15-ops-deployment.md](15-ops-deployment.md).

## 9. Permissions & Security

`POST /create-checkout-session` is explicitly `@login_required` and must pass global CSRF. This prevents anonymous checkout sessions that cannot map back to a local user.

`POST /webhook` is public and CSRF-exempt. It is intended to be authenticated by Stripe signature verification. However, signature verification is conditional: when `STRIPE_WEBHOOK_SECRET` is unset, the code parses and trusts raw JSON. That is acceptable only for local/manual development and should not be considered production-safe.

Gunicorn/Docker/Procfile access log formats use `%(U)s` rather than full request line/query string. Tests assert query strings are not logged, which matters for OAuth and token-bearing URLs.

Secrets must come from environment. `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PRICE_ID` are listed as optional integration env vars in README. They should not be committed.

## 10. Business Rules

- Billing currently stores subscription state but does not gate product features.
- A user can register without email; Stripe checkout receives `customer_email=None` in that case.
- Success page display does not prove webhook completion.
- Subscription revocation depends on `stripe_sub` being stored during activation.
- Payment failure does not revoke immediately; the code intentionally lets Stripe retry.
- Unknown Stripe events are ignored with HTTP 200.
- If Stripe API key is missing, webhook returns HTTP 400 even if signature secret is present.
- If webhook helper fails to mark/revoke a user, Stripe still receives HTTP 200 because the helper catches and logs exceptions.
- The landing page's testimonials, social proof, and `87%` statistic are static marketing copy; no source or metric calculation was found in repo.

## 11. Config & Environment

| Env var | Default | Behavior when unset |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | Empty | Checkout cannot start; webhook returns `Stripe not configured`, HTTP 400. |
| `STRIPE_WEBHOOK_SECRET` | Empty | Webhook skips Stripe signature verification and trusts JSON body. |
| `STRIPE_PRICE_ID` | Empty | Checkout redirects to pricing with "Stripe price not configured." |
| `FITNESS_DASHBOARD_SINGLE_USER` | true | When a user already exists, `/register` returns 403 unless this is false. |
| `SECRET_KEY` | See auth PRD | Needed for sessions/CSRF form token. |
| `DATA_DIR` | App directory | Determines `auth.db` location for subscription flags. |

## 12. Test Coverage

Existing tests cover that the pricing page checkout form includes `csrf_token`, that all server-rendered POST forms include a CSRF token, that Stripe revocation helper uses `auth.AUTH_DB` under `DATA_DIR`, that revocation sets `[is_pro, stripe_sub]` to `[0, None]`, and that Docker/Procfile access logs avoid query strings.

Coverage gaps:

- No test proves `stripe_bp` is registered with the app.
- No test proves `/landing`, `/pricing`, `/success`, `/cancel`, or `/webhook` are reachable in the real Flask app.
- No test verifies webhook signature is required in production.
- No test covers `checkout.session.completed` upgrading a user through the route.
- No test covers idempotent duplicate webhook delivery.
- No test proves Pro state gates or intentionally does not gate any features.
- No test verifies `/success?session_id=...` against Stripe or local entitlement.

## 13. Gaps & Issue Candidates

### IC-1: Register or remove the Stripe blueprint contract
- **Status:** Resolved as dormant by owner decision (FIT-299).
- **Decision:** `stripe_bp` remains intentionally unregistered. The Stripe routes are no longer public-auth allowlist entries, so anonymous browser requests to `/pricing`, `/success`, `/cancel`, and `/webhook` are directed through the normal login guard instead of returning anonymous 404s.
- **Dependency fact:** The `stripe` PyPI package is not installed and is absent from `requirements.txt`; registering `stripe_bp` today would fail when `stripe_checkout.py` imports `stripe`.
- **Guard:** `tests/test_fit299_stripe_dormancy.py` verifies the anonymous login behavior and that no app URL rule registers `/webhook`.

### IC-2: Require signed Stripe webhooks outside local development
- **Type:** Bug
- **Priority:** urgent
- **Where:** `stripe_checkout.py:73-86`
- **Problem:** When `STRIPE_WEBHOOK_SECRET` is unset, the webhook parses raw JSON and can mark a user Pro or revoke a subscription without verifying Stripe's signature.
- **Why it matters:** A public webhook must not accept entitlement changes from unsigned requests.
- **Acceptance criteria:**
  - Production/default behavior rejects webhooks when `STRIPE_WEBHOOK_SECRET` is missing.
  - Any unsigned local-dev mode is explicit and documented.
  - Tests cover missing secret, invalid signature, valid signature, and ignored events.
- **Duplicate-of:** FIT-255

### IC-3: Make Pro entitlement gates honest and explicit
- **Type:** Data-contract
- **Priority:** high
- **Where:** `auth.py:134-141`, `stripe_checkout.py:112-121`, app feature routes
- **Problem:** Billing writes `users.is_pro`, but no inspected feature route or UI condition reads it to enforce Free vs Pro differences. Pricing and landing claim Pro controls Oura, recommendations, unlimited history, SaaS multi-user access, and support.
- **Why it matters:** The product is either overpromising paid value or missing enforcement.
- **Acceptance criteria:**
  - Decide whether subscription is currently decorative, informational, or enforced.
  - If enforced, list exact gated features and add route/UI checks.
  - If not enforced, revise copy and docs to say billing is experimental/dormant.
- **Duplicate-of:** none

### IC-4: Add a real `/landing` route or remove the public page
- **Type:** Bug
- **Priority:** medium
- **Where:** `templates/landing.html`, `auth.py:344`
- **Problem:** A complete landing template exists and `/landing` is public, but no route renders the template in the current checkout.
- **Why it matters:** The public acquisition surface cannot be relied on by operators or tests.
- **Acceptance criteria:**
  - `/landing` renders the template or the template/public allowlist entry is removed.
  - Tests cover the chosen route behavior.
  - Navigation between landing, login, register, and pricing is verified.
- **Duplicate-of:** none

### IC-5: Verify checkout success before claiming Pro is active
- **Type:** Improvement
- **Priority:** medium
- **Where:** `stripe_checkout.py:61-63`, `templates/checkout_success.html:31-34`
- **Problem:** The success page accepts any `session_id` query value, does not fetch Stripe session state, and tells the user Pro is active even if the webhook has not marked the local account.
- **Why it matters:** The owner can see a false success state after webhook delay, webhook failure, or direct URL access.
- **Acceptance criteria:**
  - Success page checks local `current_user.is_pro` or verified Stripe session status before claiming activation.
  - Unverified success shows a pending/refresh state.
  - Tests cover direct access, missing session ID, valid webhook-updated user, and webhook-pending user.
- **Duplicate-of:** none

### IC-6: Add webhook idempotency and event audit trail
- **Type:** Improvement
- **Priority:** medium
- **Where:** `stripe_checkout.py:91-109`, `auth.py:84-112`
- **Problem:** Webhook processing does not store Stripe event IDs, event timestamps, or processing status. Duplicate delivery is mostly harmless for current mark/revoke writes, but there is no audit trail or replay protection.
- **Why it matters:** Billing support needs to explain why an entitlement changed and safely handle repeated Stripe delivery.
- **Acceptance criteria:**
  - Persist processed Stripe event IDs and status in a local table under `DATA_DIR`.
  - Duplicate events return 200 without reapplying side effects.
  - Operator docs describe where to inspect billing event history.
- **Duplicate-of:** none

### IC-7: Remove unsourced public metrics and testimonials or back them
- **Type:** Docs
- **Priority:** low
- **Where:** `templates/landing.html:474-490`, `templates/landing.html:631-660`
- **Problem:** The landing page includes static social proof, named testimonials, and an `87%` volume-goal statistic, but no source, fixture, or calculation exists in the repo.
- **Why it matters:** Public marketing copy should not imply measured outcomes that the product cannot substantiate.
- **Acceptance criteria:**
  - Replace unsupported claims with product capability copy, or document and link a real source.
  - Add a static-content test to catch reintroduction of unsourced metrics.
  - Keep single-owner/local-first positioning consistent with current product state.
- **Duplicate-of:** none

### IC-8: Align pricing copy with the single-owner app model
- **Type:** Docs
- **Priority:** medium
- **Where:** `templates/pricing.html:90-92`, `templates/landing.html:739-743`
- **Problem:** Pricing promises SaaS multi-user access and trial expiration behavior, while current docs and data model describe a single-owner local-first app with no trial-expiration store.
- **Why it matters:** The billing surface sets expectations the current app cannot satisfy.
- **Acceptance criteria:**
  - Pricing copy matches actual local-first capabilities.
  - If trial expiration is desired, add explicit trial state and enforcement.
  - If multi-user is desired, create a separate data isolation/security spec before marketing it.
- **Duplicate-of:** none
