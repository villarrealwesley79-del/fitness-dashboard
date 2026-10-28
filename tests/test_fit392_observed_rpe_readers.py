"""FIT-392: non-AI RPE readers must not treat a prescribed (unobserved) RPE as effort."""
import importlib

import pytest


@pytest.fixture()
def module():
    return importlib.import_module("app")


def _workout(date, sets):
    return {"date": date, "exercises": [{"machine": "Chest Press", "sets": sets}]}


def test_observed_set_rpe_only_counts_observed_or_legacy_rows(module):
    assert module._observed_set_rpe({"rpe": 8, "rpe_observed": True}) == 8
    assert module._observed_set_rpe({"rpe": 8}) == 8  # legacy row without the flag
    assert module._observed_set_rpe({"rpe": 8, "rpe_observed": False}) is None
    assert module._observed_set_rpe({"rpe": None, "rpe_observed": True}) is None
    assert module._observed_set_rpe({"rpe": 10 ** 400}) is None


def test_last_n_sessions_rpe_skips_unobserved_sets(module, monkeypatch):
    monkeypatch.setattr(module, "WORKOUTS", [
        _workout("2026-07-16", [{"rpe": 9, "rpe_observed": False}, {"rpe": 7, "rpe_observed": True}]),
        _workout("2026-07-15", [{"rpe": 9, "rpe_observed": False}]),
        _workout("2026-07-14", [{"rpe": 8}]),
    ])

    assert module._last_n_sessions_rpe(3) == [7.0, 8.0]
