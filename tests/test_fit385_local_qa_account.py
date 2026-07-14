import pytest
from flask import Flask
from werkzeug.security import check_password_hash


QA_ENV = (
    "FITNESS_DASHBOARD_LOCAL_QA_ENABLED",
    "FITNESS_DASHBOARD_LOCAL_QA_USERNAME",
    "FITNESS_DASHBOARD_LOCAL_QA_PASSWORD",
)
_owner_password = "owner-password"
_qa_password = "qa-password"


@pytest.fixture
def isolated_auth(tmp_path, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setattr(auth, "_owner_config_error_logged", False)
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)
    for name in QA_ENV:
        monkeypatch.delenv(name, raising=False)
    auth.init_auth_db()
    auth.User.create("owner", _owner_password)
    return auth


def _table_exists(auth, name):
    with auth._get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone() is not None


def _enable_qa(monkeypatch, *, username="agent-qa", password=_qa_password):
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "true")
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_USERNAME", username)
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_PASSWORD", password)


@pytest.fixture
def qa_app(isolated_auth, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit385-test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    _enable_qa(monkeypatch)
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/")
    def index():
        return "dashboard"

    @app.route("/protected")
    def protected():
        return "protected"

    @app.route("/api/protected")
    def api_protected():
        return {"status": "protected"}

    isolated_auth.init_auth(app)
    return app, isolated_auth


def test_default_boot_does_not_create_local_qa_schema(isolated_auth):
    assert not _table_exists(isolated_auth, "local_qa_account")


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


def test_repeated_enabled_boot_reuses_designated_user_id(isolated_auth, monkeypatch):
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        first = conn.execute(
            """
            SELECT local_qa_account.user_id, users.password
            FROM local_qa_account
            JOIN users ON users.id = local_qa_account.user_id
            WHERE local_qa_account.singleton = 1
            """
        ).fetchone()

    isolated_auth.init_auth_db()

    with isolated_auth._get_db() as conn:
        second = conn.execute(
            """
            SELECT local_qa_account.user_id, users.password
            FROM local_qa_account
            JOIN users ON users.id = local_qa_account.user_id
            WHERE local_qa_account.singleton = 1
            """
        ).fetchone()
        qa_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'agent-qa'"
        ).fetchone()[0]
    assert second["user_id"] == first["user_id"]
    assert second["password"] == first["password"]
    assert qa_count == 1


def test_enabled_boot_rotates_only_designated_credentials(isolated_auth, monkeypatch):
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        qa_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]

    _enable_qa(
        monkeypatch,
        username="agent-qa-rotated",
        password="new-password",
    )
    isolated_auth.init_auth_db()

    rotated = isolated_auth.User.authenticate("agent-qa-rotated", "new-password")
    assert rotated is not None
    assert rotated.id == qa_id
    assert isolated_auth.User.authenticate("agent-qa", "qa-password") is None
    assert isolated_auth.User.authenticate("owner", "owner-password").id != qa_id


def test_disabled_boot_removes_only_designated_account(isolated_auth, monkeypatch):
    isolated_auth.User.create("unrelated", "unrelated-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        qa_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]

    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "false")
    isolated_auth.init_auth_db()

    assert isolated_auth.User.get_by_id(qa_id) is None
    assert isolated_auth.User.authenticate("owner", "owner-password") is not None
    assert (
        isolated_auth.User.authenticate("unrelated", "unrelated-password") is not None
    )
    assert not _table_exists(isolated_auth, "local_qa_account")

    isolated_auth.init_auth_db()

    assert isolated_auth.User.authenticate("owner", "owner-password") is not None
    assert not _table_exists(isolated_auth, "local_qa_account")


def test_disabled_boot_refuses_mapping_to_owner(isolated_auth, monkeypatch):
    owner = isolated_auth.User.authenticate("owner", "owner-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        qa_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]
        conn.execute(
            "UPDATE local_qa_account SET user_id = ? WHERE singleton = 1",
            (owner.id,),
        )
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "false")

    with pytest.raises(RuntimeError, match="cleanup refused"):
        isolated_auth.init_auth_db()

    assert isolated_auth.User.get_by_id(owner.id) is not None
    assert isolated_auth.User.get_by_id(qa_id) is not None
    assert _table_exists(isolated_auth, "local_qa_account")


@pytest.mark.parametrize(
    ("username", "password", "message"),
    [
        (None, "qa-password", "requires username and password"),
        ("agent-qa", None, "requires username and password"),
        ("agent-qa", "short", "at least 8 characters"),
    ],
)
def test_invalid_qa_credentials_roll_back_without_leaking_values(
    isolated_auth, monkeypatch, username, password, message
):
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "true")
    if username is None:
        monkeypatch.delenv("FITNESS_DASHBOARD_LOCAL_QA_USERNAME", raising=False)
    else:
        monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_USERNAME", username)
    if password is None:
        monkeypatch.delenv("FITNESS_DASHBOARD_LOCAL_QA_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_PASSWORD", password)

    with pytest.raises(RuntimeError, match=message) as error:
        isolated_auth.init_auth_db()

    error_text = str(error.value)
    if username:
        assert username not in error_text
    if password:
        assert password not in error_text
    with isolated_auth._get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert not _table_exists(isolated_auth, "local_qa_account")


@pytest.mark.parametrize(
    ("owner_setting", "delete_owner", "message"),
    [
        (None, True, "requires an existing owner"),
        ("not-an-integer", False, "requires a valid owner user ID"),
        ("999", False, "requires an existing owner"),
    ],
)
def test_enabled_boot_requires_valid_existing_owner(
    isolated_auth, monkeypatch, owner_setting, delete_owner, message
):
    if delete_owner:
        with isolated_auth._get_db() as conn:
            conn.execute("DELETE FROM users")
    if owner_setting is not None:
        monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", owner_setting)
    _enable_qa(monkeypatch)
    with isolated_auth._get_db() as conn:
        original_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    with pytest.raises(RuntimeError, match=message):
        isolated_auth.init_auth_db()

    with isolated_auth._get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == original_count
    assert not _table_exists(isolated_auth, "local_qa_account")


@pytest.mark.parametrize(("username", "create_unrelated"), [("owner", False), ("taken", True)])
def test_enabled_boot_rejects_username_collision_without_partial_rows(
    isolated_auth, monkeypatch, username, create_unrelated
):
    if create_unrelated:
        isolated_auth.User.create(username, "unrelated-password")
    _enable_qa(monkeypatch, username=username)
    with isolated_auth._get_db() as conn:
        original_rows = conn.execute(
            "SELECT id, username FROM users ORDER BY id"
        ).fetchall()

    with pytest.raises(RuntimeError, match="username collides") as error:
        isolated_auth.init_auth_db()

    assert username not in str(error.value)
    with isolated_auth._get_db() as conn:
        current_rows = conn.execute(
            "SELECT id, username FROM users ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in current_rows] == [tuple(row) for row in original_rows]
    assert not _table_exists(isolated_auth, "local_qa_account")


def test_rotation_rejects_unrelated_username_and_preserves_designation(
    isolated_auth, monkeypatch
):
    isolated_auth.User.create("taken", "unrelated-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        qa_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]
    _enable_qa(monkeypatch, username="taken", password="new-password")

    with pytest.raises(RuntimeError, match="username collides"):
        isolated_auth.init_auth_db()

    assert isolated_auth.User.authenticate("agent-qa", "qa-password").id == qa_id
    assert isolated_auth.User.authenticate("taken", "unrelated-password") is not None


def test_enabled_boot_repairs_mapping_whose_user_row_is_missing(
    isolated_auth, monkeypatch
):
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        stale_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]
        conn.execute("DELETE FROM users WHERE id = ?", (stale_id,))

    isolated_auth.init_auth_db()

    with isolated_auth._get_db() as conn:
        repaired_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]
        qa_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'agent-qa'"
        ).fetchone()[0]
    assert repaired_id != stale_id
    assert qa_count == 1
    assert isolated_auth.User.authenticate("agent-qa", "qa-password").id == repaired_id


def test_enabled_boot_rolls_back_schema_when_password_hashing_fails(
    isolated_auth, monkeypatch
):
    _enable_qa(monkeypatch)

    def fail_hash(_password):
        raise RuntimeError("forced hash failure")

    monkeypatch.setattr(isolated_auth, "_hash_password", fail_hash)

    with pytest.raises(RuntimeError, match="forced hash failure"):
        isolated_auth.init_auth_db()

    with isolated_auth._get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert not _table_exists(isolated_auth, "local_qa_account")


def test_enabled_boot_rejects_singleton_mapping_to_owner(isolated_auth, monkeypatch):
    owner = isolated_auth.User.authenticate("owner", "owner-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    with isolated_auth._get_db() as conn:
        qa_id = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()["user_id"]
        conn.execute(
            "UPDATE local_qa_account SET user_id = ? WHERE singleton = 1",
            (owner.id,),
        )

    with pytest.raises(RuntimeError, match="points to the owner"):
        isolated_auth.init_auth_db()

    assert isolated_auth.User.get_by_id(owner.id) is not None
    assert isolated_auth.User.get_by_id(qa_id) is not None


def test_designated_qa_is_not_the_owner(qa_app):
    _, auth = qa_app
    qa_id = auth._local_qa_user_id()
    assert auth._is_local_qa_user_id(qa_id)
    assert not auth._is_owner_user_id(qa_id)


def test_designated_qa_login_can_open_browser_and_api_routes(qa_app):
    app, auth = qa_app
    client = app.test_client()

    response = client.post(
        "/login",
        data={"username": "agent-qa", "password": _qa_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )

    assert response.status_code == 302
    assert client.get("/protected").status_code == 200
    assert client.get("/api/protected").status_code == 200


def test_owner_login_remains_allowed_while_qa_is_enabled(qa_app):
    app, auth = qa_app
    client = app.test_client()

    response = client.post(
        "/login",
        data={"username": "owner", "password": _owner_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )

    assert response.status_code == 302
    assert client.get("/protected").status_code == 200


def test_username_match_without_singleton_designation_is_forbidden(qa_app):
    app, auth = qa_app
    auth.User.create("designated-instead", "other-password")
    other = auth.User.authenticate("designated-instead", "other-password")
    with auth._get_db() as conn:
        conn.execute(
            "UPDATE local_qa_account SET user_id = ? WHERE singleton = 1",
            (other.id,),
        )
    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": "agent-qa", "password": _qa_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )
    assert response.status_code == 302
    assert client.get("/protected").status_code == 403
    assert client.get("/api/protected").status_code == 403


def test_disabling_qa_invalidates_existing_session_on_next_request(
    qa_app, monkeypatch
):
    app, auth = qa_app
    client = app.test_client()
    client.post(
        "/login",
        data={"username": "agent-qa", "password": _qa_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )
    monkeypatch.setenv("FITNESS_DASHBOARD_LOCAL_QA_ENABLED", "false")
    auth.init_auth_db()

    response = client.get("/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/protected")


def test_runtime_invalid_owner_configuration_denies_qa_route(qa_app, monkeypatch):
    app, auth = qa_app
    client = app.test_client()
    client.post(
        "/login",
        data={"username": "agent-qa", "password": _qa_password},
        headers={auth.CSRF_HEADER_NAME: auth.CSRF_HEADER_VALUE},
    )
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", "invalid")

    assert client.get("/protected").status_code == 403


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


def test_designated_qa_reads_and_mutates_owner_food_logs(
    isolated_auth, monkeypatch, tmp_path
):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    owner = isolated_auth.User.authenticate("owner", "owner-password")
    _enable_qa(monkeypatch)
    isolated_auth.init_auth_db()
    qa_id = isolated_auth._local_qa_user_id()
    resolved_qa_id = isolated_auth.data_user_id_for(qa_id)
    data_store.add_food_log(
        user_id=owner.id,
        record={"date": "2026-07-14", "calories": 500, "protein_g": 30},
    )

    assert len(data_store.get_food_logs(user_id=resolved_qa_id)) == 1
    data_store.clear_food_logs(user_id=resolved_qa_id)

    assert data_store.get_food_logs(user_id=owner.id) == []
    assert data_store.get_food_logs(user_id=qa_id) == []


def test_app_current_data_user_id_resolves_designated_qa_to_owner(qa_app):
    import app as dashboard_app
    from flask_login import login_user

    flask_app, auth = qa_app
    owner = auth.User.authenticate("owner", "owner-password")
    qa_user = auth.User.get_by_id(auth._local_qa_user_id())

    with flask_app.test_request_context("/"):
        login_user(qa_user)
        assert dashboard_app._current_data_user_id() == owner.id


def test_app_current_data_user_id_does_not_fallback_on_invalid_qa_owner(
    qa_app, monkeypatch
):
    import app as dashboard_app
    from flask_login import login_user

    flask_app, auth = qa_app
    qa_user = auth.User.get_by_id(auth._local_qa_user_id())
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", "invalid")

    with flask_app.test_request_context("/"):
        login_user(qa_user)
        with pytest.raises(RuntimeError, match="valid owner user ID"):
            dashboard_app._current_data_user_id()
