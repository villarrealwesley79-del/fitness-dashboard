#!/usr/bin/env python3
"""FIT-2 contract checks for honest freshness states."""

import datetime as dt
import os
import sys

os.environ.setdefault("SECRET_KEY", "fit2-contract-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit2-contract-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


NOW = dt.datetime(2026, 5, 18, 12, 0, 0)


def main():
    assert app._classify_freshness(NOW - dt.timedelta(hours=2), now=NOW) == "fresh"
    assert app._classify_freshness(NOW - dt.timedelta(hours=30), now=NOW) == "aging"
    assert app._classify_freshness(NOW - dt.timedelta(hours=72), now=NOW) == "stale"
    assert app._classify_freshness(None, now=NOW) == "missing"
    print("FIT2_FRESHNESS_CONTRACT_OK")


if __name__ == "__main__":
    main()
