from __future__ import annotations

import importlib
import logging


def test_food_log_read_failure_logs_once_and_preserves_legacy_fallback(monkeypatch, caplog):
    monkeypatch.setenv("SECRET_KEY", "fit50-contract-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module._food_log_read_failure_logged = False
    monkeypatch.setattr(
        module,
        "NUTRITION_DATA",
        [{"date": "2026-05-18", "calories": 900, "protein_g": 55, "accepted": True}],
    )
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)

    def fail_get_food_logs(*_args, **_kwargs):
        raise RuntimeError("food log db unavailable")

    monkeypatch.setattr(module, "get_food_logs", fail_get_food_logs)

    with caplog.at_level(logging.WARNING):
        first = module._food_log_entries_for_context(since="2026-05-18")
        second = module._food_log_entries_for_context(since="2026-05-18")
        freshness = module._latest_food_freshness(now=module._parse_iso_date_or_datetime("2026-05-18T12:00:00"))

    assert first == []
    assert second == []
    assert freshness == ("fresh", "2026-05-18", None)
    assert caplog.messages.count("food_logs read failed; falling back to legacy nutrition JSON") == 1
