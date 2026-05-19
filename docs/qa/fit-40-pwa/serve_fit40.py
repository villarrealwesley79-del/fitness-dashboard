#!/usr/bin/env python3
"""Local QA launcher for the FIT-40 PWA Web Push permission UI.

Boots `app.py` with auth disabled so a preview tool can drive every
push-permission state (unsupported / needs_install / prompt / granted_active
/ granted_inactive / revoked / denied) and the alert preview rows without
touching `auth.db`. Mirrors the FIT-11 pattern at
`docs/qa/fit-11-mobile/serve_fit11.py`.

Run from a checkout of this repo:

    python3 docs/qa/fit-40-pwa/serve_fit40.py

The script picks a port via the `PORT` env var (default 5084 — clear of
5080-5083 used by other long-running QA processes on the maintainer's
machine).
"""
import os
import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

os.environ.setdefault("SECRET_KEY", "fit40-qa-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit40-qa-token")
os.environ.setdefault("PORT", "5084")
os.environ.setdefault("HOST", "127.0.0.1")

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from app import app  # noqa: E402

app.config["LOGIN_DISABLED"] = True

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    host = os.environ["HOST"]
    app.run(host=host, port=port, debug=False)
