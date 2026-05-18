#!/usr/bin/env python3
"""FIT-2 contract checks for honest freshness and food states."""

import copy
import datetime as dt
import inspect
import os
import sys

os.environ.setdefault("SECRET_KEY", "fit2-contract-secret")
os.environ.setdefault("HEALTH_SYNC_TOKEN", "fit2-contract-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


NOW = dt.datetime(2026, 5, 18, 12, 0, 0)


def food_state(entries):
    original = copy.deepcopy(app.NUTRITION_DATA)
    try:
        app.NUTRITION_DATA[:] = entries
        return app._compute_data_freshness(now=NOW)["food"]
    finally:
        app.NUTRITION_DATA[:] = original


def main():
    assert app._classify_freshness(NOW - dt.timedelta(hours=2), now=NOW) == "fresh"
    assert app._classify_freshness(NOW - dt.timedelta(hours=30), now=NOW) == "aging"
    assert app._classify_freshness(NOW - dt.timedelta(hours=72), now=NOW) == "stale"
    assert app._classify_freshness(None, now=NOW) == "missing"
    assert app._oura_source_label((NOW - dt.timedelta(minutes=20)).isoformat(), now=NOW) == "live"
    assert app._oura_source_label((NOW - dt.timedelta(hours=3)).isoformat(), now=NOW) == "cached"

    none = food_state([])
    assert none["status"] == "missing" and none["target_state"] == "none" and none["pending_review"] is False, none

    pending = food_state([
        {"date": "2026-05-18", "calories": 900, "protein_g": 35, "review_state": "pending"}
    ])
    assert pending["pending_review"] is True, pending
    assert pending["target_state"] == "none", pending
    assert pending["calories"] == 0, pending

    under = food_state([
        {"logged_at": "2026-05-18T08:15:00", "calories": 1000, "protein_g": 60, "status": "accepted"}
    ])
    assert under["pending_review"] is False and under["target_state"] == "under", under

    over = food_state([
        {"date": "2026-05-18", "calories": 2500, "protein_g": 160, "accepted": True}
    ])
    assert over["target_state"] == "over", over

    apple_src = inspect.getsource(app._latest_apple_health_freshness)
    assert "ah_sync_log" in apple_src and "ah_sync_events" in apple_src, apple_src
    print("FIT2_FRESHNESS_CONTRACT_OK")


if __name__ == "__main__":
    main()
