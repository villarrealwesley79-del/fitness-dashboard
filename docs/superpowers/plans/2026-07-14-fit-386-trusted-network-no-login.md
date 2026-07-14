# FIT-386 Trusted-Network No-Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit trusted-network no-login mode that resolves every protected request as the existing owner without creating an authentication session or changing the default login path.

**Architecture:** `auth.py` will parse one exact opt-in environment flag and, before the existing login guard, load the existing owner row into Flask-Login's request context. The existing guard and data-user selection stay authoritative; failure to resolve the owner falls back to normal authentication. Focused tests pin both enabled and disabled behavior before the repository-wide suite runs.

**Tech Stack:** Python 3, Flask, Flask-Login 0.6.3, SQLite, pytest.

## Global Constraints

- Work only on FIT-386 in branch `villarrealwesley79/fit-386-add-an-opt-in-no-login-mode-for-the-owners-trusted-network`.
- `FITNESS_DASHBOARD_NO_LOGIN` is disabled unless its trimmed, case-insensitive value is exactly `true`.
- Reuse the existing owner row selected by `FITNESS_DASHBOARD_OWNER_USER_ID` or the lowest user ID; never create, copy, or remap an account.
- Never use the FIT-385 QA account as the no-login identity.
- Do not call `login_user()` or store `_user_id` in the Flask session for no-login access.
- Keep normal login, registration, 401, redirect, and non-owner 403 behavior unchanged while the flag is off.
- Keep CSRF enforcement enabled and keep factory preview/CI auth configuration unchanged.
- Do not add or upgrade dependencies.
- Do not change bind addresses, Tailscale configuration, deterministic coaching, or recommendation behavior.

## File Structure

- Modify `auth.py`: flag parsing, owner resolution, request-scoped owner injection, fail-closed logging, and no-login CSRF-session suppression.
- Create `tests/test_fit386_trusted_network_no_login.py`: focused flag, identity, session, failure, default-path, and factory-preview regression coverage.
- Modify `docs/prd/01-auth-and-account.md`: as-built auth contract and security warning.
- Modify `README.md`: operator enable/disable instructions and trusted-network warning.

## Authorization Invariant Matrix

| Entry path | Flag and stored state | Canonical identity | Side effects | Expected response and proof |
| --- | --- | --- | --- | --- |
| Protected browser/API, anonymous | Flag unset/false/malformed | None | Existing CSRF/session behavior only | Browser redirect or API 401; focused default tests |
| Protected browser/API, authenticated non-owner | Flag off | Session user | No new side effects | 403; focused regression test |
| Protected route, clean client | Flag exactly `true`; valid owner row | Existing owner row | No auth DB mutation, no `_user_id`, no new session cookie | 200 and owner ID/email; enabled test |
| Protected route, stale non-owner cookie | Flag exactly `true`; valid owner row | Existing owner row overrides request only | Existing cookie is not rewritten or cleared | 200 as owner while enabled; returns to 403 after disabling flag |
| Protected route | Flag exactly `true`; invalid configured owner ID | None | One actionable log; no account creation | Existing redirect/401 path; failure test |
| Protected route | Flag exactly `true`; no owner or nonexistent owner row | None | One actionable log; no guessing or fallback account | Existing redirect/401 path; failure tests |
| Any route | Flag exactly `true`; auth DB lookup raises `sqlite3.Error`, with or without a stored session | Request-local anonymous user | One actionable exception log; no account or session mutation | Existing public/redirect/401 behavior instead of any repeated DB read or 500; clean-client and stored-session failure tests |
| Protected route | Flag exactly `true`; owner lookup succeeds once | Existing owner row and validated request marker | No second owner DB lookup in the login guard | 200 as owner even if a redundant second lookup would fail; focused single-lookup test |
| Protected route | Flag exactly `true`; `Host` is localhost, loopback, Tailscale `100.64.0.0/10`, or `*.ts.net`; direct peer is loopback or Tailscale `100.64.0.0/10` | Existing owner row | Request-local identity only | 200 as owner; trusted-host and trusted-peer tests |
| Protected browser/API | Flag exactly `true`; `Host` is attacker-controlled, LAN-only, or a deceptive `.ts.net` suffix, or trusted `Host` is spoofed by an untrusted direct peer | None unless normal session auth succeeds | No owner lookup or injection | Existing redirect/401 behavior; untrusted-host, peer-spoofing, and DNS-rebinding tests |
| Protected GET API | Flag exactly `true`; trusted `Host`; mismatched `Origin` or `Sec-Fetch-Site: cross-site` | None unless normal session auth succeeds | No owner lookup or injection | Existing API 401 despite wildcard CORS response; cross-origin read tests |
| Protected GET API | Flag exactly `true`; trusted `Host`; same-origin `Origin` or `Sec-Fetch-Site: same-origin` | Existing owner row | Request-local identity only | 200 as owner; same-origin tests |
| Protected route | Flag exactly `true`; forwarded/proxy header present, or loopback peer claims `*.ts.net` | None unless normal session auth succeeds | No owner lookup or injection | Existing redirect/401 behavior; reverse-proxy tests |
| WHOOP/Open Wearables callback | Flag exactly `true`; direct trusted request; cross-site GET with non-empty `state` and `code` | Existing owner row | Existing callback consumes user-bound, single-use OAuth state | Successful callback continues; end-to-end no-login OAuth test |
| Factory preview/CI | Existing `.agents/factory.yaml` | Existing preview login | No config mutation | Source assertion that no-login flag is absent |

Retry, concurrency, transaction, and rollback dimensions are not applicable: each request performs read-only owner lookup and request-local identity assignment; no persistent state is written.

---

### Task 1: Write focused RED tests for the full auth matrix

**Files:**
- Create: `tests/test_fit386_trusted_network_no_login.py`

**Interfaces:**
- Consumes: existing `auth.User`, `auth.init_auth(app)`, Flask `current_user`, and the existing `/login` flow.
- Produces: failing tests that require `_trusted_no_login_enabled()`, request-scoped owner injection, fail-closed behavior, and no authentication session.

- [ ] **Step 1: Create an isolated Flask test app**

Use a temporary auth database and define protected browser/API routes whose responses expose the effective identity and session state:

```python
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template_string, session
from flask_login import current_user

import auth


def _make_auth_app(tmp_path, monkeypatch, *, no_login=None):
    monkeypatch.setattr(auth, "AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("SECRET_KEY", "fit386-test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
    if no_login is None:
        monkeypatch.delenv("FITNESS_DASHBOARD_NO_LOGIN", raising=False)
    else:
        monkeypatch.setenv("FITNESS_DASHBOARD_NO_LOGIN", no_login)
    auth._no_login_owner_error_logged = False

    templates = Path(__file__).resolve().parents[1] / "templates"
    app = Flask(__name__, template_folder=str(templates))
    app.config.update(TESTING=True)

    @app.get("/protected")
    def protected():
        return jsonify(
            user_id=current_user.get_id(),
            username=current_user.username,
            email=current_user.email,
            session_user_id=session.get("_user_id"),
        )

    @app.get("/api/protected")
    def api_protected():
        return jsonify(user_id=current_user.get_id())

    @app.get("/protected-template")
    def protected_template():
        return render_template_string("owner={{ current_user.get_id() }}")

    auth.init_auth(app)
    return app
```

- [ ] **Step 2: Add explicit flag parsing tests**

```python
@pytest.mark.parametrize("value", [None, "", "false", "1", "yes", "tru"])
def test_no_login_requires_explicit_true(tmp_path, monkeypatch, value):
    app = _make_auth_app(tmp_path, monkeypatch, no_login=value)
    auth.User.create("owner", "existing-password", email="owner@example.test")

    response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")


@pytest.mark.parametrize("value", ["true", " TRUE ", "True"])
def test_no_login_accepts_only_true_case_insensitively(tmp_path, monkeypatch, value):
    app = _make_auth_app(tmp_path, monkeypatch, no_login=value)
    auth.User.create("owner", "existing-password", email="owner@example.test")

    response = app.test_client().get("/protected")

    assert response.status_code == 200
```

- [ ] **Step 3: Add enabled owner identity and no-session tests**

```python
def test_enabled_mode_uses_existing_owner_without_auth_session(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("owner", "existing-password", email="owner@example.test")
    owner = auth.User.get_by_username("owner")

    response = app.test_client().get("/protected")

    assert response.status_code == 200
    assert response.get_json() == {
        "user_id": str(owner.id),
        "username": "owner",
        "email": "owner@example.test",
        "session_user_id": None,
    }

    rendered = app.test_client().get("/protected-template")
    assert rendered.status_code == 200
    assert rendered.get_data(as_text=True) == f"owner={owner.id}"
    assert "Set-Cookie" not in rendered.headers


def test_enabled_mode_uses_configured_owner_not_first_user(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    auth.User.create("first", "existing-password")
    auth.User.create("owner", "existing-password", email="owner@example.test")
    owner = auth.User.get_by_username("owner")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", str(owner.id))

    response = app.test_client().get("/protected")

    assert response.status_code == 200
    assert response.get_json()["user_id"] == str(owner.id)
    assert response.get_json()["email"] == "owner@example.test"
```

- [ ] **Step 4: Add stale-session override and disabled-path tests**

```python
def test_enabled_mode_overrides_non_owner_session_only_for_current_mode(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    client = app.test_client()
    member = auth.User.get_by_username("member")
    with client.session_transaction() as stored_session:
        stored_session["_user_id"] = str(member.id)
        stored_session["_fresh"] = True

    monkeypatch.setenv("FITNESS_DASHBOARD_NO_LOGIN", "true")
    enabled = client.get("/protected")
    monkeypatch.delenv("FITNESS_DASHBOARD_NO_LOGIN")
    disabled = client.get("/protected")

    assert enabled.status_code == 200
    assert enabled.get_json()["username"] == "owner"
    assert disabled.status_code == 403


def test_default_keeps_browser_redirect_api_401_and_non_owner_403(tmp_path, monkeypatch):
    app = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    anonymous = app.test_client()

    assert anonymous.get("/protected").status_code == 302
    assert anonymous.get("/api/protected").status_code == 401

    member = app.test_client()
    member_user = auth.User.get_by_username("member")
    with member.session_transaction() as stored_session:
        stored_session["_user_id"] = str(member_user.id)
        stored_session["_fresh"] = True
    assert member.get("/protected").status_code == 403
```

- [ ] **Step 5: Add fail-closed owner-resolution tests and factory-config assertion**

```python
@pytest.mark.parametrize("owner_value", [None, "not-an-integer", "999"])
def test_enabled_mode_falls_back_to_login_without_valid_owner(tmp_path, monkeypatch, owner_value):
    app = _make_auth_app(tmp_path, monkeypatch, no_login="true")
    if owner_value is not None:
        monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", owner_value)

    response = app.test_client().get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")


def test_factory_preview_does_not_enable_no_login():
    config = (Path(__file__).resolve().parents[1] / ".agents" / "factory.yaml").read_text()
    assert "FITNESS_DASHBOARD_NO_LOGIN" not in config
```

Add two database-failure cases before implementation. Patch `_owner_user_id()` and `User.get_by_id()` separately to raise `sqlite3.OperationalError`, then assert a protected request redirects to login instead of returning 500 and logs that normal authentication remains enabled.

Add sibling-path RED coverage for a stored session whose `User.get_by_id()` read fails and for an owner-ID resolver that succeeds once but raises on a redundant second call. The first must redirect through a request-local anonymous identity; the second must return 200 after exactly one validated owner lookup.

Add host-boundary RED coverage before owner injection: `localhost`, loopback IPs, `100.90.15.93`, and `admins-mac-mini.tail6c6490.ts.net` are trusted; `evil.example`, `evil.ts.net.attacker.example`, and a physical-LAN address are not. Untrusted browser/API requests must retain the normal redirect/401 barrier even when the flag is enabled.

Add cross-origin GET RED coverage using trusted `localhost`: a mismatched `Origin` and `Sec-Fetch-Site: cross-site` must keep the API at 401, while a matching origin and `Sec-Fetch-Site: same-origin` must return 200 as the owner. Reuse `_has_cross_origin_browser_header()` rather than adding a second origin parser.

Add direct-peer RED coverage because `Host` is client-controlled: requests from `192.168.1.50` that claim `localhost` or `*.ts.net` must remain at 401, while a `100.64.0.0/10` peer with a trusted Tailnet host must receive owner access. Read only `REMOTE_ADDR`; do not trust forwarded-address headers without an explicit proxy contract.

Add reverse-proxy RED coverage: a loopback peer claiming `*.ts.net` and a loopback request carrying standard forwarded headers must retain the normal 401 barrier. This mode is direct-connect only and must not operate behind Tailscale Serve/Funnel.

Add an end-to-end no-login WHOOP callback RED test. Start OAuth as the request-scoped owner, return on the exact callback with `Sec-Fetch-Site: cross-site`, and prove the existing user-bound state is consumed and the connection succeeds. No other cross-origin route is exempt.

- [ ] **Step 6: Run the tests to prove RED**

Run: `/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit386_trusted_network_no_login.py`

Expected: FAIL because `_trusted_no_login_enabled()` and request-scoped owner injection do not exist; enabled requests still redirect.

---

### Task 2: Implement the minimal request-scoped owner mode

**Files:**
- Modify: `auth.py`
- Test: `tests/test_fit386_trusted_network_no_login.py`

**Interfaces:**
- Consumes: `_owner_user_id()`, `_INVALID_OWNER_USER_ID`, `User.get_by_id()`, `login_manager`, and the existing `require_login()` guard.
- Produces: `_trusted_no_login_enabled() -> bool`, `_trusted_no_login_owner() -> User | None`, and the `load_trusted_no_login_owner()` request hook.

- [ ] **Step 1: Add exact opt-in parsing and fail-closed owner resolution**

Add beside the existing owner helpers:

```python
_no_login_owner_error_logged = False


def _trusted_no_login_enabled() -> bool:
    return os.environ.get("FITNESS_DASHBOARD_NO_LOGIN", "").strip().lower() == "true"


def _trusted_no_login_owner():
    global _no_login_owner_error_logged

    owner_id = _owner_user_id()
    owner = None
    if owner_id is not _INVALID_OWNER_USER_ID and owner_id is not None:
        owner = User.get_by_id(owner_id)
    if owner is not None:
        return owner

    if not _no_login_owner_error_logged:
        logging.getLogger(__name__).error(
            "FITNESS_DASHBOARD_NO_LOGIN=true but no valid owner account could be loaded; "
            "normal authentication remains enabled"
        )
        _no_login_owner_error_logged = True
    return None
```

- [ ] **Step 2: Register owner injection before the existing login guard**

Import `g` inside `init_auth()` and register this hook after `init_auth_db()` but before the context processor and `require_login()` hooks:

```python
    @app.before_request
    def load_trusted_no_login_owner():
        if not _trusted_no_login_enabled():
            return None
        owner = _trusted_no_login_owner()
        if owner is None:
            return None
        login_manager._update_request_context_with_user(owner)
        g._trusted_no_login_owner = True
        return None
```

This intentionally uses Flask-Login 0.6.3's request-context updater so a stored session cannot override the trusted owner and no session is written.

- [ ] **Step 3: Prevent unused CSRF token creation after successful injection**

Keep normal and fail-closed behavior unchanged while avoiding a clean dashboard cookie caused only by the eager context processor:

```python
    @app.context_processor
    def inject_csrf_token():
        if getattr(g, "_trusted_no_login_owner", False):
            return {CSRF_FORM_FIELD: ""}
        return {CSRF_FORM_FIELD: _form_csrf_token()}
```

- [ ] **Step 4: Run focused GREEN proof**

Run: `/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit386_trusted_network_no_login.py tests/test_auth_login.py`

Expected: all focused FIT-386 and existing auth tests pass.

- [ ] **Step 5: Commit the tested auth behavior**

```bash
git add auth.py tests/test_fit386_trusted_network_no_login.py
git commit -m "Add FIT-386 trusted-network owner access"
```

---

### Task 3: Document the operator contract and security boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/prd/01-auth-and-account.md`

**Interfaces:**
- Consumes: the exact `FITNESS_DASHBOARD_NO_LOGIN=true` runtime contract from Task 2.
- Produces: operator enable/disable instructions and as-built auth documentation.

- [ ] **Step 1: Add concise README operator instructions**

Add a trusted-network section with exact commands and warning text:

````markdown
### Trusted-network no-login mode

For the owner's private localhost or Tailnet boot only, set:

```bash
FITNESS_DASHBOARD_NO_LOGIN=true python3 app.py
```

This uses the existing owner account and all of its workout history; it does not create or select the QA account. Unset the variable and restart to restore the login screen.

**Security warning:** This removes the login barrier for everyone who can reach the running app. Never enable it on a public, shared, port-forwarded, or otherwise untrusted network bind.
````

- [ ] **Step 2: Update the auth PRD**

Add the exact environment contract to the configuration table and the current-behavior sections:

```markdown
| `FITNESS_DASHBOARD_NO_LOGIN` | Empty | Only the trimmed, case-insensitive literal `true` loads the existing owner into the current request without creating an authentication session. Every other value keeps normal login behavior. |

When trusted-network no-login mode is enabled and a valid owner row exists, protected requests use that exact existing owner identity. Owner lookup failures fall back to normal authentication; the app never creates or guesses an account.

**Security warning:** `FITNESS_DASHBOARD_NO_LOGIN=true` removes the login barrier for everyone who can reach the instance. Use it only on the owner's localhost or private Tailnet boot, never on a public or otherwise untrusted bind.
```

The PRD must state that `LOGIN_DISABLED` remains a test convention and is not used for FIT-386.

- [ ] **Step 3: Run documentation and focused regression checks**

Run: `git diff --check`

Expected: exit 0.

Run: `/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit386_trusted_network_no_login.py tests/test_auth_login.py tests/test_factory_runtime_config.py`

Expected: all tests pass.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/prd/01-auth-and-account.md
git commit -m "Document FIT-386 trusted-network mode"
```

---

### Task 4: Complete factory proof and publication

**Files:**
- Verify all FIT-386 branch changes.

**Interfaces:**
- Consumes: the complete committed FIT-386 implementation and documentation.
- Produces: exact-head proof, clean autoreview, Draft PR, and Linear built handoff.

- [ ] **Step 1: Review scope and run focused proof**

Run: `git diff --check origin/main...HEAD`

Expected: exit 0.

Run: `/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit386_trusted_network_no_login.py tests/test_auth_login.py tests/test_factory_runtime_config.py`

Expected: all tests pass.

- [ ] **Step 2: Run the configured repository check**

Run: `/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q`

Expected: full suite passes.

- [ ] **Step 3: Run artifact safety and local-diff autoreview**

Run the installed artifact-safety checker over `origin/main...HEAD`, then run required autoreview. Resolve every accepted finding with a new focused RED/GREEN cycle and rerun the configured check after the final review-driven change.

Expected: safety passes and autoreview reports no accepted/actionable findings.

- [ ] **Step 4: Push and open one Draft PR**

Push the exact Linear branch without force. Create a Draft PR targeting `main` with literal `Closes FIT-386`, the acceptance criteria, tests, risk, agent involvement, untested items, and the exact head SHA.

- [ ] **Step 5: Run exact-head branch autoreview and hosted checks**

Read back the pushed PR head, run branch-mode autoreview against that exact SHA, post the standalone GitHub review audit, and confirm hosted checks and mergeability for the same head.

Expected: exact-head review clean, hosted checks green, PR remains Draft, and GitHub reports a non-conflict merge state.

- [ ] **Step 6: Complete the Linear handoff and release only this build lock**

Move FIT-386 to In Review, replace `agent-ready` with `built`, preserve the repository label, post branch/worktree/PR/base/head/check evidence, and read everything back. Release build lock run `18D55DA4-8D53-4624-B944-80AC8A4A6BF6` and stop before factory-review.
