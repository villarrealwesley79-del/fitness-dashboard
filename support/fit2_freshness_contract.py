#!/usr/bin/env python3
"""FIT-2 contract checks for honest freshness states."""

import datetime as dt
import inspect
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
    assert app._oura_source_label((NOW - dt.timedelta(minutes=20)).isoformat(), now=NOW) == "live"
    assert app._oura_source_label((NOW - dt.timedelta(hours=3)).isoformat(), now=NOW) == "cached"

    apple_src = inspect.getsource(app._latest_apple_health_freshness)
    assert "ah_sync_log" in apple_src and "ah_sync_events" in apple_src, apple_src
    print("FIT2_FRESHNESS_CONTRACT_OK")


if __name__ == "__main__":
    main()
