# FIT-385 Local QA Account Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Apply superpowers:test-driven-development for every production-code change.

**Goal:** Add one opt-in, environment-provisioned QA login that authenticates separately while resolving all user-scoped dashboard data to the existing owner account.

**Architecture:** Reconcile one designated QA row transactionally inside the existing SQLite auth database. Keep owner identity unchanged, add a narrow route-access predicate for the designated QA identity, and resolve that identity to the owner ID only at the existing `app._current_data_user_id()` boundary. Disabled/default boots preserve the existing schema unless a previously designated QA account must be removed.

**Tech Stack:** Python 3, Flask, Flask-Login, SQLite, Werkzeug password hashing, pytest.

**Design source:** `docs/superpowers/specs/2026-07-14-fit-385-local-qa-account-design.md`

## Invariant matrix

| State | Auth identity | Owner identity | Data identity | Protected route |
| --- | --- | --- | --- | --- |
| Disabled owner | owner | owner | owner | allow |
| Disabled former QA session | missing user | owner | unresolved | login required |
| Disabled arbitrary user | arbitrary | owner | arbitrary | 403 |
| Enabled owner | owner | owner | owner | allow |
| Enabled designated QA | QA | owner | owner | allow |
| Enabled username match without designation | arbitrary | owner | arbitrary | 403 |
| Invalid owner configuration | any | invalid/locked | no privileged mapping | startup failure or 403 |

## Transaction and idempotency rules

- `init_auth_db()` remains the only boot entry point and performs QA reconciliation inside its existing `_get_db()` transaction.
- A never-enabled boot does not create `local_qa_account`.
- Enabled reconciliation validates credentials and an existing owner before creating or changing rows.
- The singleton mapping, not the username, designates the QA account.
- Repeated enabled boots reuse the same mapped `users.id`; password hashing runs only when the configured password changed.
- Disabled cleanup deletes only the mapped QA user, refuses a mapping to the owner, and drops the singleton table.
- Any exception rolls back schema and row changes. Error messages name the invalid setting class but never credential values.

### Task 1: Add isolated lifecycle tests and transactional provisioning

**Files:**
- Create: `tests/test_fit385_local_qa_account.py`
- Modify: `auth.py:252-325`
- Test: `tests/test_fit385_local_qa_account.py`

**Step 1: Write the failing default-disabled test**

Create an isolated auth fixture and assert a default boot does not create the singleton table:

```python
import sqlite3

import pytest
from werkzeug.security import check_password_hash


QA_ENV = (
    "FITNESS_DASHBOARD_LOCAL_QA_ENABLED",
    "FITNESS_DASHBOARD_LOCAL_QA_USERNAME",
    "FITNESS_DASHBOARD_LOCAL_QA_PASSWORD",
)


@pytest.fixture
def isolated_auth(tmp_path, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
    for name in QA_ENV:
        monkeypatch.delenv(name, raising=False)
    auth.init_auth_db()
    auth.User.create("owner", "owner-password")
    return auth


def _table_exists(auth, name):
    with auth._get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone() is not None


def test_default_boot_does_not_create_local_qa_schema(isolated_auth):
    assert not _table_exists(isolated_auth, "local_qa_account")
```

**Step 2: Run the test and verify RED**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py::test_default_boot_does_not_create_local_qa_schema
```

Expected: the test initially passes because it protects existing behavior. Add the enabled provisioning test below before writing production code; that test must fail because no QA row or mapping exists.

**Step 3: Write the failing enabled provisioning test**

```python
def _enable_qa(monkeypatch, *, username="agent-qa", password="qa-password"):
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "true")
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_USERNAME", username)
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_PASSWORD", password)


def test_enabled_boot_provisions_one_hashed_designated_account(
    isolated_auth, monkeypatch
):
    _enable_qa(monkeypatch)

    isolated_auth.init_auth_db()

    with isolated_auth._get_db() as conn:
        mapping = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()
        qa = conn.execute(
            "SELECT id, username, password, salt FROM users WHERE id = ?",
            (mapping["user_id"],),
        ).fetchone()
    assert qa["username"] == "agent-qa"
    assert qa["password"] != "qa-password"
    assert check_password_hash(qa["password"], "qa-password")
    assert qa["salt"] == ""
```

**Step 4: Run the enabled test and verify RED**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py::test_enabled_boot_provisions_one_hashed_designated_account
```

Expected: fail because `local_qa_account` does not exist.

**Step 5: Implement minimal transactional reconciliation**

Add private helpers in `auth.py` and call `_reconcile_local_qa_account(conn)` at the end of `init_auth_db()`:

```python
_LOCAL_QA_ENABLED = "FITNESS_DASHBOARD_LOCAL_QA_ENABLED"
_LOCAL_QA_USERNAME = "FITNESS_DASHBOARD_LOCAL_QA_USERNAME"
_LOCAL_QA_PASSWORD = "FITNESS_DASHBOARD_LOCAL_QA_PASSWORD"


def _local_qa_enabled() -> bool:
    return os.environ.get(_LOCAL_QA_ENABLED, "").strip().lower() == "true"


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _owner_user_id_from_conn(conn):
    configured = os.environ.get("FITNESS_DASHBOARD_OWNER_USER_ID", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError:
            return _INVALID_OWNER_USER_ID
    row = conn.execute("SELECT MIN(id) FROM users").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _required_existing_owner_id(conn) -> int:
    owner_id = _owner_user_id_from_conn(conn)
    if owner_id is _INVALID_OWNER_USER_ID:
        raise RuntimeError("Local QA account requires a valid owner user ID")
    if owner_id is None or conn.execute(
        "SELECT 1 FROM users WHERE id = ?", (owner_id,)
    ).fetchone() is None:
        raise RuntimeError("Local QA account requires an existing owner")
    return owner_id
```

The reconciliation function must:

```python
def _reconcile_local_qa_account(conn) -> None:
    if not _local_qa_enabled():
        _remove_local_qa_account(conn)
        return

    username = os.environ.get(_LOCAL_QA_USERNAME, "").strip()
    password = os.environ.get(_LOCAL_QA_PASSWORD, "")
    if not username or not password:
        raise RuntimeError("Local QA account requires username and password settings")
    if len(password) < 8:
        raise RuntimeError("Local QA account password must be at least 8 characters")

    owner_id = _required_existing_owner_id(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_qa_account (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            user_id INTEGER NOT NULL UNIQUE
        )
        """
    )
    # Read the singleton, reject owner/collisions, repair a missing mapped row,
    # then insert or update exactly that designated users row and upsert singleton=1.
```

Refactor `_owner_user_id()` to delegate its database branch to `_owner_user_id_from_conn(conn)` so boot reconciliation and runtime owner resolution share exactly the same parsing/fallback behavior.

**Step 6: Verify GREEN**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py::test_default_boot_does_not_create_local_qa_schema tests/test_fit385_local_qa_account.py::test_enabled_boot_provisions_one_hashed_designated_account
```

Expected: 2 passed.

### Task 2: Prove idempotency, rotation, cleanup, and fail-closed errors

**Files:**
- Modify: `tests/test_fit385_local_qa_account.py`
- Modify: `auth.py`

**Step 1: Add failing lifecycle tests one behavior at a time**

Add tests with these exact assertions:

```python
def test_repeated_enabled_boot_reuses_designated_user_id(isolated_auth, monkeypatch):
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    first_id = isolated_auth._local_qa_user_id()
    isolated_auth.init_auth_db()
    assert isolated_auth._local_qa_user_id() == first_id


def test_enabled_boot_rotates_only_designated_credentials(isolated_auth, monkeypatch):
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    qa_id = isolated_auth._local_qa_user_id()
    _enable_qa(monkeypatch, username="agent-qa-rotated", password="new-password")
    isolated_auth.init_auth_db()
    assert isolated_auth._local_qa_user_id() == qa_id
    assert isolated_auth.User.authenticate("agent-qa-rotated", "new-password").id == qa_id
    assert isolated_auth.User.authenticate("owner", "owner-password").id != qa_id


def test_disabled_boot_removes_only_designated_account(isolated_auth, monkeypatch):
    isolated_auth.User.create("unrelated", "unrelated-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    qa_id = isolated_auth._local_qa_user_id()
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "false")
    isolated_auth.init_auth_db()
    assert isolated_auth.User.get_by_id(qa_id) is None
    assert isolated_auth.User.authenticate("owner", "owner-password") is not None
    assert isolated_auth.User.authenticate("unrelated", "unrelated-password") is not None
    assert not _table_exists(isolated_auth, "local_qa_account")
```

Also cover:

- missing username;
- missing password;
- password shorter than eight characters;
- empty users table;
- non-integer `FITNESS_DASHBOARD_OWNER_USER_ID`;
- configured owner ID with no matching row;
- collision with the owner username;
- collision with an unrelated username;
- stale mapping whose user row disappeared;
- disabled cleanup whose singleton maps to the owner;
- repeated disabled boots.

For every invalid enabled configuration, snapshot `users` and `sqlite_master` before/after and assert no partial QA user/table remains. Assert exception text does not contain the configured username or password.

**Step 2: Run each new test and verify RED before its implementation**

Run the narrow node ID for each test. Expected failure must correspond to the missing lifecycle behavior, not fixture setup.

**Step 3: Implement the minimal lifecycle behavior**

Add:

```python
def _local_qa_user_id_from_conn(conn):
    if not _table_exists(conn, "local_qa_account"):
        return None
    row = conn.execute(
        "SELECT user_id FROM local_qa_account WHERE singleton = 1"
    ).fetchone()
    return int(row["user_id"]) if row else None


def _local_qa_user_id():
    if not _local_qa_enabled():
        return None
    with _get_db() as conn:
        return _local_qa_user_id_from_conn(conn)


def _remove_local_qa_account(conn) -> None:
    if not _table_exists(conn, "local_qa_account"):
        return
    qa_id = _local_qa_user_id_from_conn(conn)
    if qa_id is not None:
        owner_id = _required_existing_owner_id(conn)
        if qa_id == owner_id:
            raise RuntimeError("Local QA mapping points to the owner; cleanup refused")
        conn.execute("DELETE FROM local_qa_account WHERE singleton = 1")
        conn.execute("DELETE FROM users WHERE id = ?", (qa_id,))
    conn.execute("DROP TABLE local_qa_account")
```

In enabled reconciliation, use `check_password_hash()` to avoid rehashing an unchanged password. All inserts, updates, deletes, and singleton writes use the passed connection; do not call `User.create()` from inside reconciliation because that would open a second transaction.

**Step 4: Run focused lifecycle tests**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py
```

Expected: all lifecycle tests pass.

### Task 3: Allow only the designated QA identity through owner-gated routes

**Files:**
- Modify: `tests/test_fit385_local_qa_account.py`
- Modify: `auth.py:462-481, 763-780`

**Step 1: Write failing access tests**

Build a minimal Flask app with `auth.init_auth(app)`, log in through `/login`, and assert:

```python
def test_designated_qa_login_can_open_browser_and_api_routes(qa_app):
    app, auth = qa_app
    client = app.test_client()
    qa_password = "qa-password"
    response = client.post(
        "/login",
        data={"username": "agent-qa", "password": qa_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )
    assert response.status_code == 302
    assert client.get("/protected").status_code == 200
    assert client.get("/api/protected").status_code == 200


def test_designated_qa_is_not_the_owner(qa_app):
    _, auth = qa_app
    qa_id = auth._local_qa_user_id()
    assert auth._is_local_qa_user_id(qa_id)
    assert not auth._is_owner_user_id(qa_id)


def test_username_match_without_singleton_designation_is_forbidden(qa_app):
    # Replace the singleton mapping with another user while keeping the QA username row.
    # Login as the username-matching but no-longer-designated row and assert browser/API 403.
```

Retain the existing non-owner 403 tests and owner login tests as regression coverage.

**Step 2: Verify RED**

Run the three new access tests. Expected: designated QA receives 403 and the new predicate is absent.

**Step 3: Implement the route-access predicate**

```python
def _is_local_qa_user_id(user_id) -> bool:
    if not _local_qa_enabled():
        return False
    try:
        candidate_id = int(user_id)
    except (TypeError, ValueError):
        return False
    with _get_db() as conn:
        qa_id = _local_qa_user_id_from_conn(conn)
        if qa_id != candidate_id:
            return False
        try:
            owner_id = _required_existing_owner_id(conn)
        except RuntimeError:
            return False
        return qa_id != owner_id


def _has_owner_route_access(user_id) -> bool:
    return _is_owner_user_id(user_id) or _is_local_qa_user_id(user_id)
```

Change only the global protected-route guard from `_is_owner_user_id(...)` to `_has_owner_route_access(...)`. Do not change `_is_owner_user_id()` semantics or registration rules.

**Step 4: Verify GREEN and regressions**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py tests/test_auth_login.py tests/test_auth_password_kdf.py
```

Expected: all pass.

### Task 4: Alias QA data identity to the owner at the existing app boundary

**Files:**
- Modify: `tests/test_fit385_local_qa_account.py`
- Modify: `auth.py`
- Modify: `app.py:181, 1795-1802`

**Step 1: Write failing resolver tests**

```python
def test_data_user_id_for_designated_qa_returns_owner(isolated_auth, monkeypatch):
    owner = isolated_auth.User.authenticate("owner", "owner-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    qa_id = isolated_auth._local_qa_user_id()
    assert isolated_auth.data_user_id_for(owner.id) == owner.id
    assert isolated_auth.data_user_id_for(qa_id) == owner.id


def test_data_user_id_for_arbitrary_user_remains_unchanged(isolated_auth):
    isolated_auth.User.create("other", "other-password")
    other = isolated_auth.User.authenticate("other", "other-password")
    assert isolated_auth.data_user_id_for(other.id) == other.id
```

Add one direct request-context test proving `app._current_data_user_id()` returns the owner ID while the designated QA user is logged in. Then write one representative owner-scoped data-store test: create a record with the owner ID, resolve the QA ID through `data_user_id_for()`, read and mutate the same record under the resolved ID, and assert no duplicate QA-owned record is created.

**Step 2: Verify RED**

Run the resolver tests. Expected: `data_user_id_for` is absent and `app._current_data_user_id()` returns the QA authentication ID.

**Step 3: Implement the resolver and wire the single boundary**

```python
def data_user_id_for(user_id) -> int:
    candidate_id = int(user_id)
    if not _local_qa_enabled():
        return candidate_id
    with _get_db() as conn:
        qa_id = _local_qa_user_id_from_conn(conn)
        if candidate_id != qa_id:
            return candidate_id
        owner_id = _required_existing_owner_id(conn)
        if qa_id == owner_id:
            raise RuntimeError("Local QA mapping cannot resolve to the owner account itself")
        return owner_id
```

Update the existing import and helper in `app.py`:

```python
from auth import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, data_user_id_for, init_auth


def _current_data_user_id():
    try:
        from flask_login import current_user
        authenticated = bool(current_user and current_user.is_authenticated)
    except RuntimeError:
        authenticated = False
    if authenticated:
        return data_user_id_for(current_user.get_id())
    return 1
```

Do not catch errors from `data_user_id_for()`: an invalid QA mapping must fail closed instead of silently falling back to owner ID `1`. Do not change the 40 existing `_current_data_user_id()` callers or direct authentication checks.

**Step 4: Verify GREEN**

Run:

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py tests/test_auth_login.py tests/test_auth_password_kdf.py
```

Expected: all pass, including the representative owner-data round trip.

### Task 5: Document the operator contract

**Files:**
- Modify: `docs/prd/01-auth-and-account.md`
- Modify: `docs/prd/appendix/enum-dictionary.md`

**Step 1: Update active auth documentation**

Document all three variable names without literal credentials:

```text
FITNESS_DASHBOARD_LOCAL_QA_ENABLED=true
FITNESS_DASHBOARD_LOCAL_QA_USERNAME=<shared-agent-qa-username>
FITNESS_DASHBOARD_LOCAL_QA_PASSWORD=<secret-at-least-8-characters>
```

State plainly:

- local testing only; never production or a public/shared deployment;
- one account is shared by testing agents;
- restart to provision or rotate;
- unset/false plus restart removes the designated QA account;
- the owner login and owner credentials remain unchanged;
- QA actions read and modify the owner's real dashboard data;
- FIT-386, not FIT-385, owns optional no-login owner boot.

Update the auth environment table, security section, business rules, test coverage, and enum dictionary.

**Step 2: Verify docs contain no credential literal**

Run:

```bash
rg -n "FITNESS_DASHBOARD_LOCAL_QA_(ENABLED|USERNAME|PASSWORD)|local testing only|never production" docs/prd/01-auth-and-account.md docs/prd/appendix/enum-dictionary.md
git diff --check
```

Expected: only variable names and placeholders appear; `git diff --check` is clean.

### Task 6: Full verification and factory handoff

**Files:**
- Verify all changed files only.

**Step 1: Run focused tests**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q tests/test_fit385_local_qa_account.py tests/test_auth_login.py tests/test_auth_password_kdf.py
```

**Step 2: Run the configured repository check**

```bash
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q
```

**Step 3: Review scope and secrets**

```bash
git diff --check
git status --short
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- auth.py app.py tests/test_fit385_local_qa_account.py docs/prd/01-auth-and-account.md docs/prd/appendix/enum-dictionary.md docs/superpowers/specs/2026-07-14-fit-385-local-qa-account-design.md docs/superpowers/plans/2026-07-14-fit-385-local-qa-account.md
```

Run the repository-required autoreview and artifact-safety checks. Fix only accepted FIT-385 findings, rerun focused/full tests, and rerun review until clean.

**Step 4: Commit and publish the exact factory handoff**

- Commit with an imperative FIT-385 subject.
- Push the issue branch without force.
- Open a Draft PR using `.github/pull_request_template.md` and `Closes FIT-385`.
- Post standalone review evidence with the review command, focused/full test results, findings, review-driven changes, and final clean result.
- Verify exact PR head SHA, hosted checks, `mergeStateStatus`, and Draft state.
- Add the Linear completion comment with branch, PR, commit, tests, review, and merge state; move FIT-385 to In Review and replace `agent-ready` with `built`.
- Release only the exact FIT-385 build lock after the handoff is durably recorded.
