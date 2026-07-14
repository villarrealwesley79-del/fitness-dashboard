from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REMOVED_FILES = (
    "stripe_checkout.py",
    "templates/pricing.html",
    "templates/checkout_success.html",
    "templates/checkout_cancel.html",
    "templates/landing.html",
    "docs/prd/13-billing-stripe-landing.md",
)
REMOVED_ROUTES = {
    "/pricing",
    "/create-checkout-session",
    "/success",
    "/cancel",
    "/webhook",
    "/landing",
}
REMOVED_USER_COLUMNS = {"is_pro", "stripe_customer", "stripe_sub"}


def test_billing_modules_templates_and_routes_stay_removed(tmp_path):
    for relative_path in REMOVED_FILES:
        assert not (REPO_ROOT / relative_path).exists(), relative_path

    assert importlib.util.find_spec("stripe_checkout") is None

    script = r'''
import json

import app
import auth

removed = {
    "/pricing",
    "/create-checkout-session",
    "/success",
    "/cancel",
    "/webhook",
    "/landing",
}
print(json.dumps({
    "registered": sorted(removed & {rule.rule for rule in app.app.url_map.iter_rules()}),
    "public": sorted(path for path in removed if auth._is_public(path)),
    "csrf_exempt": sorted(path for path in removed if auth._is_csrf_exempt(path)),
}))
'''
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(tmp_path),
            "SECRET_KEY": "fit384-route-test-secret",
            "SESSION_COOKIE_SECURE": "false",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "registered": [],
        "public": [],
        "csrf_exempt": [],
    }


def test_existing_users_lose_only_billing_columns_and_owner_login_survives(
    tmp_path, monkeypatch
):
    import auth

    auth_db = tmp_path / "auth.db"
    owner_password = auth._hash_password("owner-password")
    qa_password = auth._hash_password("qa-password")
    with sqlite3.connect(auth_db) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                salt TEXT NOT NULL,
                email TEXT,
                is_pro INTEGER NOT NULL DEFAULT 0,
                stripe_customer TEXT,
                stripe_sub TEXT,
                created TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO users (
                id, username, password, salt, email,
                is_pro, stripe_customer, stripe_sub, created
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (7, "owner", owner_password, "", "owner@example.test", 1, "cus_owner", "sub_owner", "2026-01-01"),
                (11, "qa", qa_password, "", "qa@example.test", 0, None, None, "2026-02-02"),
            ],
        )

    monkeypatch.setattr(auth, "AUTH_DB", str(auth_db))
    monkeypatch.delenv("FITNESS_DASHBOARD_OWNER_USER_ID", raising=False)

    auth.init_auth_db()

    with sqlite3.connect(auth_db) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        rows = [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY id")]

    assert REMOVED_USER_COLUMNS.isdisjoint(columns)
    assert rows == [
        {
            "id": 7,
            "username": "owner",
            "password": owner_password,
            "salt": "",
            "email": "owner@example.test",
            "created": "2026-01-01",
        },
        {
            "id": 11,
            "username": "qa",
            "password": qa_password,
            "salt": "",
            "email": "qa@example.test",
            "created": "2026-02-02",
        },
    ]
    assert auth._owner_user_id() == 7
    monkeypatch.setenv("FITNESS_DASHBOARD_OWNER_USER_ID", "11")
    assert auth._owner_user_id() == 11
    assert auth.User.authenticate("owner", "owner-password").id == 7
    assert auth.User.get_by_username("owner").email == "owner@example.test"
