from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_auth_app(
    tmp_path,
    monkeypatch,
    *,
    secure_cookie="false",
    factory_preview=False,
):
    import auth

    auth_db = tmp_path / "auth.db"
    monkeypatch.setattr(auth, "AUTH_DB", str(auth_db))
    monkeypatch.setenv("SECRET_KEY", "fit185-auth-test-secret")
    if secure_cookie is None:
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("SESSION_COOKIE_SECURE", secure_cookie)
    if factory_preview:
        monkeypatch.setenv("FITNESS_DASHBOARD_FACTORY_PREVIEW", "1")
    else:
        monkeypatch.delenv("FITNESS_DASHBOARD_FACTORY_PREVIEW", raising=False)
    monkeypatch.setenv("FITNESS_DASHBOARD_SINGLE_USER", "true")
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)

    templates = Path(__file__).resolve().parents[1] / "templates"
    app = Flask(__name__, template_folder=str(templates))

    @app.route("/")
    def index():
        return "dashboard"

    @app.route("/protected")
    def protected():
        return "protected"

    auth.init_auth(app)
    return app, auth


def test_auth_cookie_defaults_remain_secure_without_factory_override(tmp_path, monkeypatch):
    app, _auth = _make_auth_app(tmp_path, monkeypatch, secure_cookie=None)

    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_SECURE"] is True


def test_factory_preview_seeds_fixed_owner_account(tmp_path, monkeypatch):
    app, auth = _make_auth_app(
        tmp_path,
        monkeypatch,
        secure_cookie=None,
        factory_preview=True,
    )

    preview_user = auth.User.authenticate("test", "1224")

    assert preview_user is not None
    assert preview_user.is_pro is True
    assert auth._is_owner_user_id(preview_user.id) is True
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["REMEMBER_COOKIE_SECURE"] is False


def test_ordinary_local_boot_never_seeds_factory_preview_account(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(
        tmp_path,
        monkeypatch,
        secure_cookie="false",
        factory_preview=False,
    )

    assert auth.User.get_by_username("test") is None


def test_fallback_secret_is_identical_across_cold_started_processes(tmp_path):
    secret_file = tmp_path / ".flask-secret"
    script = "import auth, sys; print(auth._load_or_create_secret(sys.argv[1]))"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(secret_file)],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    results = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0, 0, 0]
    secrets_read = [stdout.strip() for stdout, _stderr in results]
    assert len(set(secrets_read)) == 1
    assert secret_file.read_text(encoding="utf-8") == secrets_read[0]
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600


def test_existing_read_only_fallback_secret_remains_usable(tmp_path):
    import auth

    secret_file = tmp_path / ".flask-secret"
    secret_file.write_text("stable-read-only-secret", encoding="utf-8")
    secret_file.chmod(0o400)

    assert auth._load_or_create_secret(str(secret_file)) == "stable-read-only-secret"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o400


def test_owner_correct_password_authenticates(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password", email="owner@example.test")

    user = auth.User.authenticate("Wesley1226", "existing-password")

    assert user is not None
    assert user.username == "Wesley1226"
    assert user.is_pro is False


def test_wrong_password_returns_none_and_records_rate_limit_on_login(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 1)

    assert auth.User.authenticate("Wesley1226", "wrong-password") is None

    response = app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "wrong-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.10"},
    )

    assert response.status_code == 200
    assert b"Invalid username or password." in response.data
    assert auth._rate_check("ip:198.51.100.10") is False
    assert auth._rate_check("user:Wesley1226") is False


def test_login_success_sets_session_and_reaches_protected_route(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    client = app.test_client()

    response = client.post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "Set-Cookie" in response.headers
    assert client.get("/protected").status_code == 200


def test_login_post_rejects_external_next_redirect(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")

    response = app.test_client().post(
        "/login?next=https://evil.example/phish",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "evil.example" not in response.headers["Location"]


def test_login_post_rejects_backslash_next_redirect(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")

    response = app.test_client().post(
        "/login?next=/\\evil.example/phish",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "evil.example" not in response.headers["Location"]


def test_login_rejects_tab_control_char_next_redirect(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    client = app.test_client()

    client.post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
    )
    get_response = client.get("/login?next=/%09/evil.example")
    post_response = client.post(
        "/login?next=/%09/evil.example",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    for response in (get_response, post_response):
        assert response.status_code == 302
        location = urlsplit(response.headers["Location"])
        assert location.path == "/"
        assert not location.scheme
        assert not location.netloc


def test_login_rejects_cr_only_next_without_500(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")

    response = app.test_client().post(
        "/login?next=%0D",
        data={"username": "Wesley1226", "password": "existing-password"},
    )

    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/"


def test_login_rate_limit_ignores_spoofed_forwarded_for(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 2)
    client = app.test_client()

    for index in range(2):
        response = client.post(
            "/login",
            data={"username": f"unknown-{index}", "password": "wrong-password"},
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
            environ_base={"REMOTE_ADDR": "198.51.100.20"},
        )
        assert response.status_code == 200

    blocked = client.post(
        "/login",
        data={"username": "unknown-3", "password": "wrong-password"},
        headers={"X-Forwarded-For": "203.0.113.99"},
        environ_base={"REMOTE_ADDR": "198.51.100.20"},
    )

    assert blocked.status_code == 429


def test_login_rate_limit_also_keys_by_username(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 2)
    client = app.test_client()

    for index in range(2):
        response = client.post(
            "/login",
            data={"username": "Wesley1226", "password": "wrong-password"},
            headers={"X-Forwarded-For": f"203.0.113.{index}, 198.51.100.{index}"},
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
        )
        assert response.status_code == 200

    blocked = client.post(
        "/login",
        data={"username": "Wesley1226", "password": "wrong-password"},
        headers={"X-Forwarded-For": "203.0.113.99, 198.51.100.99"},
        environ_base={"REMOTE_ADDR": "10.0.0.8"},
    )

    assert blocked.status_code == 429


def test_username_rate_limit_uses_exact_login_username(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 2)
    client = app.test_client()

    for index in range(2):
        response = client.post(
            "/login",
            data={"username": "wesley1226", "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": f"198.51.100.{index}"},
        )
        assert response.status_code == 200

    allowed = client.post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.99"},
    )

    assert allowed.status_code == 302


def test_login_rate_limit_survives_separate_app_instances(tmp_path, monkeypatch):
    first_app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("Wesley1226", "existing-password")
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 2)

    for _ in range(2):
        response = first_app.test_client().post(
            "/login",
            data={"username": "Wesley1226", "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": "198.51.100.40"},
        )
        assert response.status_code == 200

    second_app, _auth = _make_auth_app(tmp_path, monkeypatch)
    blocked = second_app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.40"},
    )

    assert blocked.status_code == 429


def test_login_rate_limit_reservation_is_atomic_across_workers(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    monkeypatch.setattr(auth, "_RATE_LIMIT_MAX_FAILS", 1)
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait()
        return auth._rate_reserve_attempt_all(["ip:198.51.100.41"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(lambda _index: reserve(), range(2)))

    assert sum(reservation is not None for reservation in reservations) == 1


def test_success_does_not_erase_concurrent_pending_failure(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    rate_keys = ["ip:198.51.100.44", "user:Wesley1226"]
    pending_failure = auth._rate_reserve_attempt_all(rate_keys)
    successful_attempt = auth._rate_reserve_attempt_all(rate_keys)

    auth._rate_complete_success(successful_attempt, rate_keys)
    auth._rate_finalize_attempt(pending_failure)

    with sqlite3.connect(auth.AUTH_DB) as conn:
        rows = conn.execute(
            "SELECT DISTINCT attempt_id, status FROM auth_rate_limit_attempts"
        ).fetchall()
    assert rows == [(pending_failure, "failed")]


def test_login_rate_limit_database_failure_returns_503(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)

    def fail_reservation(_rate_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth, "_rate_reserve_attempt_all", fail_reservation)
    response = app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.42"},
    )

    assert response.status_code == 503
    assert b"Login service temporarily unavailable." in response.data


def test_registration_rate_limit_database_failure_returns_503(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)

    def fail_reservation(_rate_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth, "_rate_reserve_attempt_all", fail_reservation)
    response = app.test_client().post(
        "/register",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.43"},
    )

    assert response.status_code == 503
    assert b"Registration service temporarily unavailable." in response.data


def test_registration_cleanup_failure_does_not_report_failure_after_commit(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)

    def fail_cleanup(_attempt_id, _rate_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(auth, "_rate_complete_success", fail_cleanup)
    response = app.test_client().post(
        "/register",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.45"},
    )

    assert response.status_code == 302
    assert auth.User.get_by_username("Wesley1226") is not None


def test_rate_check_evicts_empty_pruned_keys(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    stale_key = "ip:198.51.100.30"
    now = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: now - auth._RATE_LIMIT_WINDOW_SEC - 1)
    auth._rate_record_fail(stale_key)
    monkeypatch.setattr(auth.time, "time", lambda: now)

    assert auth._rate_check(stale_key) is True
    with sqlite3.connect(auth.AUTH_DB) as conn:
        attempt_count = conn.execute("SELECT COUNT(*) FROM auth_rate_limit_attempts").fetchone()[0]
    assert attempt_count == 0


def test_auth_db_migrates_existing_rate_limit_rows_without_attempt_ids(tmp_path, monkeypatch):
    import auth

    auth_db = tmp_path / "auth.db"
    with sqlite3.connect(auth_db) as conn:
        conn.execute(
            """
            CREATE TABLE auth_rate_limit_attempts (
                identity_hash TEXT NOT NULL,
                attempted_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO auth_rate_limit_attempts (identity_hash, attempted_at) VALUES (?, ?)",
            ("legacy-hash", time.time()),
        )
    monkeypatch.setattr(auth, "AUTH_DB", str(auth_db))

    auth.init_auth_db()

    with sqlite3.connect(auth_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(auth_rate_limit_attempts)")}
        migrated = conn.execute(
            "SELECT attempt_id, identity_hash FROM auth_rate_limit_attempts"
        ).fetchone()
    assert "attempt_id" in columns
    assert migrated[0]
    assert migrated[1] == "legacy-hash"


def test_invalid_owner_id_locks_authenticated_non_owner_and_logs_error(tmp_path, monkeypatch, caplog):
    app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    member = auth.User.get_by_username("member")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", "not-an-integer")

    client = app.test_client()
    assert client.post(
        "/login", data={"username": "member", "password": "existing-password"}
    ).status_code == 302

    with caplog.at_level(logging.ERROR, logger=auth.__name__):
        response = client.get("/protected")

    assert response.status_code == 403
    assert auth._is_owner_user_id(member.id) is False
    assert "FITNESS_DASHBOARD_OWNER_USER_ID is set but not an integer" in caplog.text


def test_unset_owner_id_uses_lowest_user_id(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("owner", "existing-password")
    auth.User.create("member", "existing-password")
    owner = auth.User.get_by_username("owner")
    member = auth.User.get_by_username("member")

    assert auth._is_owner_user_id(owner.id) is True
    assert auth._is_owner_user_id(member.id) is False


def test_valid_owner_id_allows_only_configured_user(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)
    auth.User.create("first", "existing-password")
    auth.User.create("owner", "existing-password")
    first_user = auth.User.get_by_username("first")
    configured_owner = auth.User.get_by_username("owner")
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", str(configured_owner.id))

    assert auth._is_owner_user_id(configured_owner.id) is True
    assert auth._is_owner_user_id(first_user.id) is False


def test_no_users_remains_permissive_for_first_run_setup(tmp_path, monkeypatch):
    _app, auth = _make_auth_app(tmp_path, monkeypatch)

    assert auth._is_owner_user_id("first-run") is True


def test_login_db_unavailable_returns_503_not_invalid_credentials(tmp_path, monkeypatch):
    app, auth = _make_auth_app(tmp_path, monkeypatch)

    def fail_authenticate(_username, _password):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(auth.User, "authenticate", fail_authenticate)

    response = app.test_client().post(
        "/login",
        data={"username": "Wesley1226", "password": "existing-password"},
        environ_base={"REMOTE_ADDR": "198.51.100.11"},
    )

    assert response.status_code == 503
    assert b"Login service temporarily unavailable." in response.data
    assert b"Invalid username or password." not in response.data
    with sqlite3.connect(auth.AUTH_DB) as conn:
        attempt_count = conn.execute("SELECT COUNT(*) FROM auth_rate_limit_attempts").fetchone()[0]
    assert attempt_count == 0
