# FIT-386 Trusted-Network No-Login Design

## Goal

Add an explicitly enabled personal-use mode that serves protected routes as the existing owner without requiring a login. The mode must reuse the owner's real auth identity and data user ID so workout history, nutrition, recovery, settings, and owner-only behavior remain unchanged.

This mode is only for the owner's localhost or private Tailnet boot. It does not create a new account, use the FIT-385 QA account, change factory previews, or alter the default authentication path.

## Existing Behavior

`auth.init_auth()` initializes Flask-Login and registers a global `require_login()` guard. With the current default configuration, unauthenticated browser requests redirect to `/login`, API requests receive 401, and authenticated non-owners receive 403.

`LOGIN_DISABLED` skips that guard, but it leaves `current_user` anonymous. It therefore cannot satisfy FIT-386 because application code reads `current_user.get_id()`, `current_user.email`, and `_current_data_user_id()` to select the owner's data.

## Approaches Considered

### 1. Request-scoped owner identity (selected)

Resolve the existing owner row at the beginning of each request and install that `User` object into Flask-Login's request context with `login_manager._update_request_context_with_user(owner)`. The repository currently runs Flask-Login 0.6.3, where that method assigns the request-local `g._login_user`. Do not call `login_user()` and do not write an authentication identity to the session.

This preserves the real owner ID and data while allowing a clean client to access protected routes without an authentication cookie. It also overrides a stale non-owner session for the current request while the mode is enabled.

The selected hook is a private Flask-Login API. That coupling is narrower than duplicating Flask-Login's user-loading machinery, and focused tests will pin the required request-local behavior so a future Flask-Login upgrade cannot silently break it.

### 2. Automatic session login (rejected)

Calling `login_user(owner)` would be simple, but it creates an authentication session cookie. That contradicts the no-session requirement and makes the feature behave like hidden automatic login rather than request-scoped trusted access.

### 3. Guard bypass only (rejected)

Reusing `LOGIN_DISABLED` would skip redirects, but `current_user` would remain anonymous. Owner-only routes and data selection could fail or use fallback identity behavior.

## Configuration Contract

The new environment variable is `FITNESS_DASHBOARD_NO_LOGIN`.

- Only the case-insensitive literal `true` enables the mode.
- Unset, empty, `false`, malformed, and misspelled values leave the mode disabled.
- Even with the flag enabled, owner injection requires a trusted localhost/Tailscale request host and a direct peer address that is independently loopback or in Tailscale's `100.64.0.0/10` device range.
- The default remains the existing login flow.
- Factory preview and CI configuration remain unchanged and do not set this variable.
- The change does not alter `HOST`, bind addresses, Tailscale configuration, or public exposure.

The application reads the environment value through one focused helper so the enablement rule is tested independently and used consistently.

## Request Flow

When the mode is disabled, no new request hook performs work. The existing Flask-Login session loader, public-route handling, unauthenticated redirects and 401 responses, and non-owner 403 checks run unchanged.

When the mode is enabled:

1. A request hook registered before `require_login()` validates the request `Host` as localhost, loopback, a Tailscale IPv4 address, or a fully qualified `*.ts.net` MagicDNS name. It independently requires the direct peer address to be loopback or in Tailscale's IPv4 device range, and rejects browser requests whose `Origin` or `Sec-Fetch-Site` proves they are cross-origin. Other requests receive normal authentication behavior.
2. The hook resolves the owner using the existing owner-selection rule: `FITNESS_DASHBOARD_OWNER_USER_ID` when valid, otherwise the lowest local user ID.
3. The hook loads that exact existing `User` row.
4. The hook installs the owner only in Flask-Login's current request context. It does not call `login_user()`, clear sessions, create an account, or mutate the auth database.
5. `current_user` therefore exposes the owner's real ID, username, email, and Pro state for the remainder of the request.
6. The existing `require_login()` guard sees an authenticated owner and uses the request marker from the already validated owner lookup instead of querying the owner row a second time. The disabled/default guard path is unchanged.
7. Application helpers such as `_current_data_user_id()` receive the owner's real ID, so all existing owner history and settings remain attached to the same account.

An existing browser session does not choose the effective identity while no-login mode is enabled; the request-scoped owner wins. Turning the environment flag off restores the normal session identity on the next process boot.

## Session and CSRF Behavior

No-login mode must not write `_user_id` or other Flask-Login authentication state into the session. A clean request to a protected non-form route must return without a `Set-Cookie` authentication response.

The current template context processor eagerly creates a session-backed CSRF token even when a rendered template does not use it. Under a successfully activated no-login request, it will avoid materializing that unused token so a clean dashboard request does not create a session cookie solely as a rendering side effect. If owner resolution fails and normal login is shown, the existing session-backed form token remains available.

The global CSRF request guard remains enabled. Existing same-origin browser checks, API request headers, form-token validation, and webhook exemptions are unchanged. Repository search confirms only the login and pricing templates currently consume the injected form token; neither is part of the protected owner dashboard flow in the selected mode.

## Failure Behavior

No-login mode fails closed into normal authentication:

- If the configured owner ID is not an integer, no owner is injected.
- If no owner row exists, no owner is injected.
- If the selected owner row cannot be loaded, no owner is injected.
- If either owner-ID lookup or owner-row lookup raises `sqlite3.Error`, no owner is injected.
- A database-error request is fixed to Flask-Login's anonymous user for that request so an existing session or remember cookie cannot trigger the same failed read again.
- The existing guard then redirects or returns 401 exactly as it does today.

The application logs one actionable error for an enabled mode that cannot resolve a valid owner. It does not guess another account, auto-create an account, fall back to the FIT-385 QA account, or expose an anonymous request as an owner.

## Public Auth Routes

With a valid request-scoped owner, a GET to `/login` follows the existing authenticated-user redirect to the dashboard. Logging out cannot disable trusted-network mode; the operator must unset `FITNESS_DASHBOARD_NO_LOGIN` and restart the app to restore the login barrier.

When owner injection fails, login and registration behave normally so first-run setup or configuration repair remains possible.

## Security Boundary

Enabling this mode deliberately removes the authentication barrier for every person or device that can reach the running instance. It is acceptable only for a localhost or private Tailnet boot controlled by the owner. It must never be enabled on a public, shared, port-forwarded, or otherwise untrusted network bind.

The request `Host` gate is defense in depth against DNS rebinding, while the independent direct-peer gate prevents an untrusted client from activating owner identity by spoofing `Host: localhost` or a `*.ts.net` name. No forwarded-address header is trusted.

The existing application sends wildcard CORS response headers, so the no-login hook must also refuse owner injection for cross-origin browser reads. Direct navigation, requests without cross-origin browser evidence, and same-origin requests remain eligible.

Documentation will place this warning next to the exact environment variable and restart instructions. No secret values, real credentials, or owner data are added to source control.

## Test Design

Focused tests will prove:

- only the explicit value `true` enables the mode;
- an enabled clean request to a protected route returns 200 without a login step or authentication session state;
- `current_user.get_id()`, username, and email are the existing owner's values;
- a configured owner ID selects that exact row rather than the first row;
- an existing non-owner session is treated as the owner only while the mode is enabled;
- `_current_data_user_id()` resolves to the same existing owner ID, preserving workout history and other owner data;
- a missing, invalid, or nonexistent owner fails into the normal login flow;
- the disabled and default cases retain browser redirects, API 401 responses, authenticated non-owner 403 responses, and working login/register pages;
- factory preview configuration does not enable no-login mode;
- a clean protected response does not create an authentication session cookie.

Verification will run the focused auth and FIT-386 tests first, followed by the repository's full `python3 -m pytest -q` suite and diff/safety checks required by the factory workflow.

## Files in Scope

- `auth.py`: explicit flag parsing, request-scoped owner injection, fail-closed logging, and avoidance of unused CSRF session state after successful injection.
- `tests/test_fit386_trusted_network_no_login.py`: focused enabled, disabled, identity, data continuity, cookie, and failure-path coverage.
- `tests/test_auth_login.py`: only if an existing auth regression assertion belongs more clearly beside the established guard tests.
- `docs/prd/01-auth-and-account.md`: configuration contract, runtime behavior, and prominent trusted-network warning.
- `README.md`: short operator-facing enable/disable instructions and the public-network warning.

## Non-Goals

- No default or production enablement.
- No public bind or Tailscale configuration changes.
- No removal or weakening of the normal login path.
- No factory preview or CI authentication changes.
- No FIT-385 QA-account changes.
- No account creation, migration, data copying, or owner-history remapping.
- No deterministic coaching or recommendation changes.
