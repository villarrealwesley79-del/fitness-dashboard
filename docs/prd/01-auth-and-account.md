# Auth & Account — PRD

> **Sources:** `auth.py`, `app.py`, `templates/login.html`, `tests/test_auth_login.py`, `tests/test_auth_password_kdf.py`, `tests/test_csrf_protection.py`, `README.md`, `docs/CURRENT_STATE.md`
> **Routes:** `/login`, `/register`, `/logout`, `/api/auth/scope`; global auth/CSRF guards apply to all routes.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Auth & Account is the gatekeeper for the local-first Fitness Dashboard. The app is designed for one owner user by default, with session login protecting the dashboard, settings, wearable status, food logs, workout actions, backup/import, and nearly every API route. Public access is intentionally narrow: sign-in, first account creation, static assets, checkout-adjacent pages, the Stripe webhook contract, and the token-authenticated Apple Health webhook.

The primary user scenario is simple: the owner creates the first account in a local runtime, signs in with a username/password, and then uses a Flask-Login browser-session cookie to access the app. If the app already has a user and single-user mode remains enabled, further registration is blocked. The first user becomes the owner unless `FITNESS_DASHBOARD_OWNER_USER_ID` explicitly selects another local row.

The implementation is local SQLite, not a hosted identity provider. Account data lives in `auth.db` under `DATA_DIR` through `runtime_config.data_path("auth.db")`; when `DATA_DIR` is unset, the store falls back to the repo/app directory. Passwords are stored with Werkzeug scrypt for new users, with legacy SHA-256+salt rows upgraded after successful login.

The auth layer also implements the app's mutation protection model. Browser writes are accepted when they include `X-Requested-With: XMLHttpRequest`, a valid server-rendered form CSRF token, or same-origin browser metadata. Explicit cross-origin browser metadata is rejected before the header check. The Apple Health webhook and Stripe webhook paths are exempt from this CSRF model because they are expected to be called by external systems and have their own authentication/signature contracts.

## 2. User-Facing Surfaces

| Surface | File | Audience | Behavior |
| --- | --- | --- | --- |
| Sign-in page | `templates/login.html` | Returning owner | Centered dark card with username, password, flash errors, CSRF hidden input, and link to Register. |
| Registration page | `templates/login.html` with `register=True` | First local account setup | Same card layout plus optional email field labelled as needed for billing. Blocks once a user exists in single-owner mode. |
| Logout | `/logout` | Signed-in owner | Requires login, clears the Flask-Login session, redirects to `/login`. |
| Dashboard auth scope | `/api/auth/scope` | Browser queue/client ownership checks | Returns a stable string `user:<id>` based on the current authenticated data user. |
| Global auth guard | `auth.init_auth(app)` before-request hook | Every route | Redirects unauthenticated browser navigation to `/login?next=<path>`; returns JSON 401 for API/JSON callers. |
| Owner-only guard | `auth.init_auth(app)` before-request hook | Multi-row local DBs | In single-user mode, blocks authenticated non-owner rows with 403. |

The login/register template is server-rendered and does not contain JavaScript. Flash messages are shown in red error styling. The form always includes `csrf_token`; tests assert every server-rendered POST form includes this token.

## 3. Field Inventory

### Login Form

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `csrf_token` | hidden string | Yes | Generated in session | Must match session token unless the request is same-origin or has accepted CSRF header | Proves the form was rendered by this app session. |
| `username` | text | Yes in browser | Empty string | Trimmed before auth lookup; no length or format validation in login path | Local account username. |
| `password` | password | Yes in browser | Empty string | Checked against stored hash | Secret credential for local account. |
| `next` | query string | No | `url_for("index")` | Sanitized on authenticated GET only; not sanitized on POST success [TBC] | Post-login redirect target. |

### Registration Form

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `csrf_token` | hidden string | Yes | Generated in session | Same as login | CSRF protection for account creation. |
| `username` | text | Yes | Empty string | Trimmed; must be non-empty; must not already exist | New local account name. |
| `email` | email input | No | `None` | Browser type hint only; server trims and stores non-empty value | Optional billing/customer email for Stripe checkout. |
| `password` | password | Yes | Empty string | Must be at least 8 characters | New local account password. |

### `users` SQLite Table

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | integer primary key | Yes | Autoincrement | SQLite generated | Local user ID and Flask-Login identity. |
| `username` | text unique | Yes | None | Unique DB constraint; application checks duplicates on register | Human login name. |
| `password` | text | Yes | None | Werkzeug hash for new rows; legacy 64-char SHA-256 accepted only during migration | Stored password verifier. |
| `salt` | text | Yes | Empty string for new rows | Legacy SHA-256 rows use salt; scrypt rows keep `""` | Legacy compatibility field. |
| `email` | text | No | `NULL` | No server format validation | Billing email passed to Stripe checkout when present. |
| `is_pro` | integer boolean | Yes | `0` | Updated by Stripe helper only | Local subscription entitlement flag; no app feature gates found in current code. |
| `stripe_customer` | text | No | `NULL` | Stored from checkout webhook | Stripe customer ID linkage. |
| `stripe_sub` | text | No | `NULL` | Stored from checkout webhook; used for revocation lookup | Stripe subscription ID linkage. |
| `created` | text datetime | No | `datetime('now')` | SQLite generated | Account creation timestamp. |

## 4. Interactions & Flows

### First Account Creation

Trigger → User opens `/register` and submits username/password/email.  
Behavior → The server checks single-user mode. If `FITNESS_DASHBOARD_SINGLE_USER` is not `"false"` and any user row exists, the request returns the login template with HTTP 403 and the message "Registration is disabled for this single-owner dashboard."  
Validation → Rate limit is checked per client IP. Username and password are required. Password must be at least 8 characters. Username must be unique.  
API → `POST /register`.  
Success → Creates the user with scrypt password hash, clears the IP failure log, authenticates, logs in, and redirects to `/`.  
Failure → Missing fields or short password flash inline messages. Duplicate username records a failed attempt. Lockout returns HTTP 429.

### Login

Trigger → User submits `/login`.  
Behavior → Server identifies IP from the first `X-Forwarded-For` value or `request.remote_addr`, checks rate limit, authenticates against `auth.db`, and starts a Flask-Login session.  
Validation → Per-IP lockout after 10 failed attempts in 10 minutes. DB errors are not presented as invalid credentials.  
API → `POST /login`.  
Success → Failed-attempt history for that IP is cleared, `login_user(user)` is called, and the user is redirected to `next` or `/`.  
Failure → Invalid credentials flash "Invalid username or password." and return login page with HTTP 200. Rate limit returns HTTP 429. SQLite auth DB error returns HTTP 503.

### Authenticated GET `/login`

Trigger → Already-authenticated user opens `/login`.  
Behavior → Redirects to `next` query param or `/`; non-local or protocol-relative redirects are normalized to `/`. A bare `/` is rewritten to `/?fd_shell_reload=20260525-fit181-controller-reload-r2` to force a cached shell refresh.  
Validation → `next` must start with a single `/`.  
API → `GET /login`.  
Success → Redirect to sanitized target.

### Logout

Trigger → Authenticated user opens `/logout`.  
Behavior → Calls `logout_user()`.  
Validation → Route is decorated with `@login_required`.  
API → `GET /logout`.  
Success → Redirects to `/login`.  
Failure → If already unauthenticated, global/public route behavior allows the path because `/logout` is public, but the Flask-Login decorator can redirect to login [TBC: exact redirect behavior depends on Flask-Login runtime].

### Global Auth Guard

Trigger → Any request after `init_auth(app)`.  
Behavior → Public paths pass through. Unauthenticated API/JSON requests receive `{"error":"Unauthorized","login":"/login"}` with HTTP 401. Unauthenticated browser requests redirect to `/login?next=<path>`. Authenticated non-owner requests in single-user mode receive HTTP 403.  
Validation → Owner is `FITNESS_DASHBOARD_OWNER_USER_ID` when set to an integer; otherwise minimum user ID. An invalid non-integer value is a logged misconfiguration and fails closed: authenticated users are denied owner-only routes until it is corrected. Only an unset/empty value falls back to minimum `users.id`. If there is no owner row yet, access is allowed so first setup can proceed.

### CSRF / Origin Protection

Trigger → Any `POST`, `PUT`, `PATCH`, or `DELETE`.  
Behavior → Exempt paths pass through. Cross-site browser metadata is rejected. Otherwise request is accepted if any of these are true: `X-Requested-With` equals `XMLHttpRequest`, form token matches the session token, or browser `Origin`/`Referer`/`Sec-Fetch-Site` indicates same-origin.  
Validation → Same-origin includes `request.host_url`, configured `FITNESS_DASHBOARD_PUBLIC_BASE_URL`, and client-supplied `X-Forwarded-Host`/`X-Forwarded-Proto` headers when present (accepted without proxy-trust validation).  
API → Global before-request hook.  
Success → Request reaches route handler.  
Failure → API/JSON callers receive HTTP 403 JSON with code `csrf_required`; HTML callers receive plain `Forbidden`.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/login` | Public | Open sign-in page or already-authenticated redirect | `next` query | HTML login page or 302 redirect | Real |
| POST | `/login` | Public + CSRF | Submit credentials | `username`, `password`, `csrf_token`, `next` query | HTML with flash, 302 redirect, 429, or 503 | Real |
| GET | `/register` | Public until account exists | Open registration page | None | HTML registration page or 403 login page | Real |
| POST | `/register` | Public + CSRF until account exists | Create first account | `username`, `email`, `password`, `csrf_token` | HTML with flash, 302 redirect, 403, or 429 | Real |
| GET | `/logout` | Login required | Sign out | None | 302 to `/login` | Real |
| GET | `/api/auth/scope` | Login required + owner guard | Client queue ownership checks | None | `{"auth_scope":"user:<id>"}` | Real |

Non-obvious behavior:

- Login and registration rate limiting stores hashed IP/username identities in `auth.db`, so the active 10-minute window is shared across workers and survives process restarts. Expired rows are pruned on subsequent auth activity, and successful auth clears the matching identities.
- Login uses `X-Forwarded-For` before `remote_addr`, but there is no trusted-proxy check in the auth module.
- POST login redirects to `next` without the same open-redirect normalization used by authenticated GET `/login`.
- The public allowlist includes `/landing`, `/pricing`, `/webhook`, `/success`, and `/cancel`; `stripe_bp` is defined but never registered in this checkout, so `/pricing`, `/webhook`, `/success`, and `/cancel` 404 even though they are public-allowlisted. Reachability details are documented in [13-billing-stripe-landing.md](13-billing-stripe-landing.md).

## 6. Data Model & Persistence

Auth persistence is `AUTH_DB = data_path("auth.db")`. `runtime_config.DATA_DIR` is `os.environ["DATA_DIR"]` when set, otherwise the app directory. `data_path()` creates the directory before returning the path.

`init_auth_db()` creates and migrates the `users` table and creates `auth_rate_limit_attempts` with an identity/time index. Rate-limit rows contain SECRET_KEY-keyed HMAC-SHA-256 identity digests and timestamps, not raw IP addresses or usernames. Rotating `SECRET_KEY` makes prior digests unreachable and therefore resets the remaining active lockout window; expired/unreachable rows are pruned on later auth activity. The user migration adds `email`, `is_pro`, `stripe_customer`, and `stripe_sub` if missing. It does not remove legacy columns or backfill existing emails. The auth module commits through a context-managed SQLite connection and rolls back on exceptions.

Password persistence rules:

- New users: `generate_password_hash(password, method="scrypt:32768:8:1")`.
- Legacy rows: a 64-character hex digest is treated as SHA-256 of `salt + password`.
- Legacy verification uses `hmac.compare_digest`.
- Successful legacy login updates `password` to the scrypt hash and `salt` to an empty string.

Session persistence is Flask's signed cookie session. Secret key resolution is:

1. `SECRET_KEY` environment variable.
2. `.flask-secret` file next to `auth.py`.
3. Generated 128-character hex secret persisted to `.flask-secret` with mode `0600`.

Existing fallback files are read under a shared lock, including valid read-only mounts. Empty/missing fallback initialization holds an exclusive cross-process lock through read/generate/write/fsync, so cold-started workers cannot retain different secrets. If a new fallback cannot be persisted, startup fails and requires an explicit `SECRET_KEY`. The app also refuses to start with an empty secret or the literal default `dev-key-change-me`.

## 7. Enums & Constants

| Name | Value(s) | Meaning |
| --- | --- | --- |
| `_RATE_LIMIT_WINDOW_SEC` | `600` | Failed-login window is 10 minutes. |
| `_RATE_LIMIT_MAX_FAILS` | `10` | Lockout begins at 10 failed attempts in the active window. |
| `CSRF_HEADER_NAME` | `X-Requested-With` | Header accepted for browser mutation requests. |
| `CSRF_HEADER_VALUE` | `XMLHttpRequest` | Required CSRF header value. |
| `CSRF_FORM_FIELD` | `csrf_token` | Hidden form field for server-rendered POSTs. |
| `CSRF_SESSION_KEY` | `_auth_csrf_token` | Flask session key storing the generated form token. |
| `_CSRF_MUTATING_METHODS` | `POST`, `PUT`, `PATCH`, `DELETE` | Methods requiring CSRF/origin approval. |
| `_CSRF_EXEMPT_PATHS` | `/api/apple-health/sync`, `/webhook` | External integration paths exempt from CSRF. |
| `_PASSWORD_HASH_METHOD` | `scrypt:32768:8:1` | Werkzeug hash method for new and upgraded passwords. |
| `_PUBLIC_PREFIXES` | `/login`, `/register`, `/logout`, `/landing`, `/pricing`, `/manifest.json`, `/sw.js`, `/static/`, `/robots.txt`, `/sitemap.xml`, `/webhook`, `/success`, `/cancel`, `/api/apple-health/sync` | Routes allowed before login. Entries ending in `/` are prefix matches; others are exact. |
| `FITNESS_DASHBOARD_SINGLE_USER` | Default `"true"`; `"false"` disables owner restriction | Controls single-owner mode. |
| `FITNESS_DASHBOARD_NO_LOGIN` | Disabled unless the trimmed, case-insensitive value is exactly `"true"` | Enables request-scoped owner access for the owner's trusted-network boot. |
| `SESSION_COOKIE_SECURE` | Default true unless env equals `"false"` | Controls session and remember-cookie Secure flag. |
| Session lifetime | 14 days configured but currently inert | `session.permanent` is never set and `login_user()` never passes `remember=True`, so the session cookie is a browser-session cookie and no remember cookie is issued. |

## 8. Integration Points

Auth feeds every feature PRD because global guards run before route handlers. The client uses `/api/auth/scope` to bind local queues or cached recommendation payloads to the authenticated user. Billing uses `User.email`, `User.mark_pro()`, and `User.revoke_pro()`; see [13-billing-stripe-landing.md](13-billing-stripe-landing.md). Apple Health uses a token-authenticated public webhook exemption; detailed Apple Health behavior belongs in its integration PRD.

The app's single-user model is also a data isolation assumption: most runtime stores are not per-user partitioned. The owner guard prevents non-owner local accounts from seeing or mutating shared dashboard data in the default mode.

## 9. Permissions & Security

The default policy is private-by-default. Any path not in `_PUBLIC_PREFIXES` requires an authenticated Flask-Login user. In single-user mode, authenticated users are still blocked unless they are the configured owner or the first local user row.

Session cookies are HTTP-only, `SameSite=Lax`, and Secure by default. Local HTTP development can set `SESSION_COOKIE_SECURE=false`; production should set `SECRET_KEY` through environment or secret manager. `.flask-secret` is a local-dev fallback and is excluded by Docker hygiene tests.

CSRF protection is not a per-route decorator; it is a global before-request gate. It protects public login/register forms and authenticated API mutations. The two exempt paths rely on other authentication: Apple Health by `HEALTH_SYNC_TOKEN`; Stripe by `Stripe-Signature` when `STRIPE_WEBHOOK_SECRET` is configured. If `STRIPE_WEBHOOK_SECRET` is unset, the Stripe webhook path parses unverified JSON instead; see FIT-255 and the billing PRD.

`LOGIN_DISABLED` bypasses the global login/owner guard entirely when truthy. This is a test convention, not a production access mode.

`FITNESS_DASHBOARD_NO_LOGIN=true` is a separate, explicit personal-use mode. Before the existing login guard runs, it loads the configured owner row (or the lowest user ID) into Flask-Login's request context without calling `login_user()` or storing an authentication identity in the session. Owner lookup failures fall back to normal authentication; the app never creates, guesses, or substitutes an account.

**Security warning:** Trusted-network no-login mode removes the login barrier for everyone who can reach the instance. Use it only on the owner's localhost or private Tailnet boot, never on a public, shared, port-forwarded, or otherwise untrusted bind.

The request host is enforced before owner injection to reduce DNS-rebinding risk. Accepted hosts are `localhost`, loopback IPs, Tailscale's [`100.64.0.0/10` device range](https://tailscale.com/docs/concepts/tailscale-ip-addresses), and fully qualified [`*.ts.net` MagicDNS names](https://tailscale.com/docs/features/magicdns). Any other `Host` keeps normal authentication behavior even when the flag is set.

## 10. Business Rules

- First local user is the owner unless `FITNESS_DASHBOARD_OWNER_USER_ID` selects a valid integer ID.
- Trusted-network no-login mode uses that exact existing owner identity and data user ID, so workout history and settings remain attached to the owner account; it never selects the FIT-385 QA account.
- Trusted-network owner injection requires a localhost, loopback, Tailscale device-IP, or fully qualified MagicDNS request host; deceptive or physical-LAN hosts retain the normal login barrier.
- If trusted-network owner resolution fails, protected requests keep the normal redirect/401 behavior.
- If single-user mode is disabled, `_is_owner_user_id()` returns true for any authenticated user.
- A missing owner row allows access rather than blocking setup.
- Public prefix entries without trailing slash are exact matches; `/api/apple-health/sync/status` remains protected even though `/api/apple-health/sync` is public.
- API/JSON unauthorized responses are JSON 401; browser unauthorized responses redirect to login.
- Form-token CSRF, same-origin metadata, and `X-Requested-With` are alternative valid mutation proofs unless cross-origin metadata is detected.
- A successful login clears only the current IP's rate-limit history.
- Legacy password rows are upgraded only after successful authentication.

## 11. Config & Environment

| Env var | Default | Behavior when unset |
| --- | --- | --- |
| `DATA_DIR` | App directory | `auth.db` is created next to the app. |
| `SECRET_KEY` | None | Reads or creates `.flask-secret`; startup fails if secret stays empty/default. |
| `SESSION_COOKIE_SECURE` | `"true"` | Secure cookies enabled. Set `"false"` only for local HTTP. |
| `FITNESS_DASHBOARD_SINGLE_USER` | `"true"` | Registration closes after first account and owner guard applies. |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | Empty | Owner is minimum `users.id`. An invalid non-integer value logs an actionable error and locks owner-only routes until corrected. Only unset/empty falls back to minimum `users.id`; an empty users table remains permissive for first-run setup. |
| `FITNESS_DASHBOARD_NO_LOGIN` | Empty | Only the trimmed, case-insensitive literal `"true"` loads the existing owner into the current request without creating an authentication session. Every other value keeps normal login behavior. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | Empty | Adds a trusted same-origin value for CSRF origin comparison when set. |

## 12. Test Coverage

Existing focused tests cover successful login/session access, wrong password behavior, rate-limit recording, DB-unavailable 503 behavior, new-user scrypt storage, legacy SHA-256 migration, constant-time legacy compare, CSRF rejection/allowance paths, checkout form CSRF token presence, public auth form tokens, WHOOP mutation CSRF enforcement, and live JS sending the CSRF header.

FIT-386 coverage additionally pins explicit no-login enablement, localhost/Tailscale host enforcement, DNS-rebinding-style host rejection, request-scoped owner identity, configured-owner selection, no authentication session or clean-template cookie, stale non-owner session override while enabled, fail-closed owner lookup, unchanged default redirect/401/403 behavior, and the absence of no-login enablement from factory preview configuration.

Coverage gaps:

- No focused test proves POST `/login?next=...` rejects external or protocol-relative redirects.
- No focused test proves `X-Forwarded-For` spoofing cannot bypass rate limiting behind an untrusted client.
- No test exercises cross-worker or post-restart rate-limit semantics.
- No test asserts `/landing` is reachable or intentionally absent.
- No test validates account creation in multi-user mode with shared non-user-partitioned data stores.

## 13. Gaps & Issue Candidates

### IC-1: Sanitize POST-login redirect targets
- **Type:** Bug
- **Priority:** urgent
- **Where:** `auth.py:293`
- **Problem:** POST `/login` redirects to the `next` query parameter without the same local-path normalization used by authenticated GET `/login`. A crafted login URL can send the owner to an external or protocol-relative target after successful authentication.
- **Why it matters:** Login should not become a phishing/open-redirect primitive.
- **Acceptance criteria:**
  - POST login accepts only single-slash local paths for `next`.
  - Unsafe `next` values fall back to `/`.
  - Tests cover absolute URL, protocol-relative URL, malformed URL, and normal local path.
- **Duplicate-of:** FIT-255

### IC-2: Stop trusting raw X-Forwarded-For for login throttling
- **Type:** Bug
- **Priority:** high
- **Where:** `auth.py:278`, `auth.py:307`
- **Problem:** Login and registration rate limiting use the first `X-Forwarded-For` value whenever present. Without trusted-proxy validation, a direct client can spoof a new IP on each request and avoid the in-memory lockout.
- **Why it matters:** The current rate limiter is weaker than the UI message implies.
- **Acceptance criteria:**
  - Direct requests use `remote_addr` unless the app is explicitly behind a trusted proxy.
  - Trusted proxy configuration is documented and tested.
  - Login and registration share the corrected client identity helper.
- **Duplicate-of:** FIT-255

### IC-3: Persist or centralize auth rate limiting for multi-worker runtime
- **Type:** Improvement
- **Priority:** medium
- **Where:** `auth.py:21-44`, `Dockerfile:33-36`
- **Problem:** Failed-attempt state is an in-memory Python dictionary. It resets on restart and is not shared across the two gunicorn workers configured in the Dockerfile.
- **Why it matters:** The product promises lockout behavior, but production-like multi-worker runtime weakens it.
- **Acceptance criteria:**
  - Rate-limit state survives worker selection for the configured deployment mode.
  - Restart semantics are documented honestly if persistence is intentionally not added.
  - Tests prove lockout cannot be bypassed by separate app instances when the selected backend supports it.
- **Duplicate-of:** none

### IC-4: Add an account-owner repair path for multi-row local auth DBs
- **Type:** Improvement
- **Priority:** medium
- **Where:** `auth.py:237-258`, `docs/CURRENT_STATE.md:151`
- **Problem:** The app defaults owner identity to the minimum `users.id`, but current docs warn `auth.db` may contain more than one row. There is no visible owner repair/admin flow besides setting `FITNESS_DASHBOARD_OWNER_USER_ID`.
- **Why it matters:** A stale first row can lock the real owner out of shared local runtime data.
- **Acceptance criteria:**
  - Document an operator-safe owner recovery procedure.
  - Add a read-only diagnostic that reports owner selection without exposing secrets.
  - FIT-277 added tests for invalid, missing, and valid `FITNESS_DASHBOARD_OWNER_USER_ID`; this follow-up retains the owner-repair flow work.
- **Duplicate-of:** none

### IC-5: Make `/landing` route status explicit
- **Type:** Bug
- **Priority:** medium
- **Where:** `auth.py:344`, `templates/landing.html`
- **Problem:** The global public allowlist includes `/landing`, and a complete landing template exists, but route inventory and code search show no route registering it in this checkout.
- **Why it matters:** Operators and agents can assume a public landing page exists when the app may actually return 404.
- **Acceptance criteria:**
  - Either register a deliberate `/landing` route or remove the public allowlist/template expectation.
  - Add a route test for the chosen behavior.
  - Update billing/landing documentation to match the live route.
- **Duplicate-of:** none

### IC-6: Add explicit tests for owner-only non-owner denial
- **Type:** Test
- **Priority:** low
- **Where:** `auth.py:249-258`, `auth.py:532-536`
- **Problem:** The owner guard is central to single-owner privacy, but the listed auth tests do not directly exercise an authenticated non-owner row being denied API and browser routes.
- **Why it matters:** Most app data stores are shared local files, so this guard is the main per-account privacy boundary.
- **Acceptance criteria:**
  - Test browser route non-owner denial returns HTTP 403.
  - Test API non-owner denial returns JSON HTTP 403.
  - Test `FITNESS_DASHBOARD_SINGLE_USER=false` intentionally permits non-owner access.
- **Duplicate-of:** none
