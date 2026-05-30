from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.security import check_password_hash


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth

KDF_PREFIXES = ("scrypt:", "pbkdf2:")


@pytest.fixture()
def isolated_auth_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    auth_db = tmp_path / "auth.db"
    monkeypatch.setattr(auth, "AUTH_DB", str(auth_db))
    auth._rate_fail_log.clear()
    auth.init_auth_db()
    yield auth_db
    auth._rate_fail_log.clear()


def test_new_users_are_stored_with_slow_kdf(isolated_auth_db: Path) -> None:
    auth.User.create("owner", "correct horse battery staple", email="owner@example.com")

    row = _stored_user("owner")
    assert row["password"].startswith(KDF_PREFIXES)
    assert not auth._is_legacy_password_hash(row["password"])
    assert row["salt"] == ""
    assert check_password_hash(row["password"], "correct horse battery staple")

    user = auth.User.authenticate("owner", "correct horse battery staple")
    assert user is not None
    assert user.username == "owner"
    assert auth.User.authenticate("owner", "wrong-password") is None


def test_legacy_sha256_login_upgrades_hash_on_success(
    isolated_auth_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_legacy_user("legacy-owner", "legacy-password")

    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    monkeypatch.setenv("SECRET_KEY", "fit188-test-secret")
    auth.init_auth(app)

    response = app.test_client().post(
        "/login?next=/dashboard",
        data={"username": "legacy-owner", "password": "legacy-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"

    row = _stored_user("legacy-owner")
    assert row["password"].startswith(KDF_PREFIXES)
    assert row["salt"] == ""
    assert check_password_hash(row["password"], "legacy-password")
    assert auth.User.authenticate("legacy-owner", "wrong-password") is None


def test_legacy_sha256_comparison_uses_constant_time_compare(
    isolated_auth_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_legacy_user("legacy-owner", "legacy-password")
    real_compare = auth.hmac.compare_digest
    calls: list[tuple[str, str]] = []

    def compare_spy(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", compare_spy)

    assert auth.User.authenticate("legacy-owner", "wrong-password") is None

    row = _stored_user("legacy-owner")
    assert calls == [
        (auth._legacy_hash_password("wrong-password", row["salt"]), row["password"])
    ]
    assert auth._is_legacy_password_hash(row["password"])


def test_legacy_sha256_detection_rejects_non_digest_hex_like_strings() -> None:
    assert auth._is_legacy_password_hash("0" * 64)
    assert not auth._is_legacy_password_hash("  " + "0" * 64)
    assert not auth._is_legacy_password_hash("+" + "0" * 63)
    assert not auth._is_legacy_password_hash("0" * 63)


def _insert_legacy_user(username: str, password: str) -> None:
    salt = "legacy-salt"
    with auth._get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password, salt, email) VALUES (?, ?, ?, ?)",
            (
                username,
                auth._legacy_hash_password(password, salt),
                salt,
                f"{username}@example.com",
            ),
        )


def _stored_user(username: str):
    with auth._get_db() as conn:
        return conn.execute(
            "SELECT username, password, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()
