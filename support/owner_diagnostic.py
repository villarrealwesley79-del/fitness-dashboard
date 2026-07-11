#!/usr/bin/env python3
"""Report local auth owner selection without reading credential fields."""

import argparse
import json
import os
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_DB = Path(os.environ.get("DATA_DIR", "").strip() or REPO_ROOT) / "auth.db"


def _read_accounts(db_path: Path) -> list[dict]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
    return [{"id": int(row[0]), "username": row[1]} for row in rows]


def diagnose_owner(db_path: Path) -> dict:
    accounts = _read_accounts(db_path)
    configured = os.environ.get("FITNESS_DASHBOARD_OWNER_USER_ID", "").strip()
    try:
        configured_id = int(configured) if configured else None
    except ValueError:
        configured_id = None
        status = "invalid_configuration"
    else:
        status = None
    owner = (
        next((account for account in accounts if account["id"] == configured_id), None)
        if configured
        else (accounts[0] if accounts else None)
    )
    if status is None:
        if configured and owner is None:
            status = "configured_user_missing"
        else:
            status = "selected" if owner else "no_users"
    return {
        "accounts": accounts,
        "owner": owner,
        "selection": "configured_user_id" if configured else "minimum_user_id",
        "single_user_mode": os.environ.get("FITNESS_DASHBOARD_SINGLE_USER", "true").lower() != "false",
        "status": status,
        "user_count": len(accounts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_AUTH_DB, help="auth.db path")
    args = parser.parse_args()
    print(json.dumps(diagnose_owner(args.db), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
