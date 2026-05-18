#!/usr/bin/env python3
"""Local QA launcher for the FIT-11 mobile visual QA pass.

Boots `app.py` with auth disabled so a preview tool can drive every
modal / banner without touching `auth.db`. Run from a checkout of this
repo:

    python3 docs/qa/fit-11-mobile/serve_fit11.py

The script picks a port via the `PORT` env var (default 5081 because
5080 is held by an unrelated long-running process on the maintainer's
machine).
"""
import os
import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

os.environ.setdefault("SECRET_KEY", "fit11-qa-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit11-qa-token")
os.environ.setdefault("PORT", "5081")
os.environ.setdefault("HOST", "127.0.0.1")

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from app import app  # noqa: E402

app.config["LOGIN_DISABLED"] = True

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    host = os.environ["HOST"]
    app.run(host=host, port=port, debug=False)
