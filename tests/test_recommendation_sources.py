from datetime import date

import data_store
from open_wearables_adapter import OpenWearablesProviderStatus
from recommendation_sources import build_open_wearables_recommendation_source
from wearable_fact_store import WearableDailyFact, upsert_daily_facts


def test_open_wearables_recommendation_source_is_display_only_when_missing():
    proof = build_open_wearables_recommendation_source({"status": "missing"})
    assert proof["source"] == "open_wearables"
    assert proof["role"] == "display only"
    assert proof["influence"] == "none"


def test_open_wearables_recommendation_source_lists_generic_providers():
    proof = build_open_wearables_recommendation_source(
        {"status": "fresh"},
        [{"label": "Garmin"}, {"label": "Polar"}],
    )
    assert proof["role"] == "display only"
    assert proof["influence"] == "none"
    assert "Garmin" in proof["detail"]
    assert "Polar" in proof["detail"]


def test_open_wearables_recommendation_source_uses_only_normalized_facts():
    proof = build_open_wearables_recommendation_source(
        {"status": "fresh"},
        facts=[{"provider_id": "open_wearables", "metric": "sleep_duration", "value": 330}],
        modifier={"applied": True, "applied_modifiers": ["sleep_caution"], "detail": "Open Wearables sleep duration 330 min -> recommendation held conservative."},
    )
    assert proof["role"] == "bounded modifier"
    assert proof["influence"] == "bounded_modifier"
    assert proof["facts_used"] == 1
    assert proof["applied_modifiers"] == ["sleep_caution"]
    assert "330 min" in proof["detail"]


def _seed_open_wearables_fact_route_context(monkeypatch, tmp_path):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()

    import app as module

    db = tmp_path / "wearable-facts.sqlite3"
    today = date.today().isoformat()
    upsert_daily_facts(str(db), [
        WearableDailyFact(today, "open_wearables", "Open Wearables", "sleep_duration", 330, "min", confidence="medium", freshness="fresh", used_for_recommendation=True),
        WearableDailyFact(today, "open_wearables", "Open Wearables", "active_minutes", 95, "min", confidence="medium", freshness="fresh", used_for_recommendation=True),
    ], profile_key="1")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WEARABLE_FACTS_DB_FILE", str(db))
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "audit")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "audit")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "audit-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8018")
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([
        OpenWearablesProviderStatus(
            provider_id="open_wearables",
            label="Open Wearables",
            state="connected",
            capabilities={"sync": True},
        )
    ], None))
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "_cached_wttr", lambda _location: {"available": False})
    monkeypatch.setattr(module, "generate_next_workout", lambda _workouts, _soreness, training_recommendation=None, **_kwargs: {
        "focus": training_recommendation,
        "title": f"{training_recommendation} plan",
        "exercises": [],
    })
    return module


def test_open_wearables_facts_downgrade_smart_recommendation_route(monkeypatch, tmp_path):
    module = _seed_open_wearables_fact_route_context(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_latest_apple_health_freshness", lambda *_args, **_kwargs: ("stale", "2026-06-24", "2026-06-24T13:04:00"))

    payload = module.app.test_client().get("/api/recommendation/smart").get_json()

    assert payload["recommendation"] == "recovery"
    assert payload["next_workout"]["focus"] == "recovery"
    assert payload["recommendation_sources"]["open_wearables"]["role"] == "bounded modifier"
    assert "sleep_caution" in payload["recommendation_sources"]["open_wearables"]["applied_modifiers"]
    assert payload["recommendation_sources"]["load_source"] == "apple_health"
    assert payload["recommendation_sources"]["load_source_summary_hidden"] is True
    assert payload["recommendation_sources"]["whoop"]["display_only"] is True
    assert payload["recommendation_sources"]["whoop"]["summary_hidden"] is True


def test_open_wearables_facts_downgrade_dashboard_and_next_workout_routes(monkeypatch, tmp_path):
    module = _seed_open_wearables_fact_route_context(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: "moderate")
    monkeypatch.setattr(module, "_latest_apple_health_freshness", lambda *_args, **_kwargs: ("stale", "2026-06-24", "2026-06-24T13:04:00"))

    dashboard = module.app.test_client().get("/api/dashboard").get_json()
    module.LAST_WORKOUT_RECOMMENDATION = None
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = None
    next_workout = module.app.test_client().get("/api/next-workout").get_json()

    assert dashboard["next_workout"]["focus"] == "recovery"
    assert dashboard["recommendation_sources"]["open_wearables"]["role"] == "bounded modifier"
    assert dashboard["recommendation_sources"]["load_source_summary_hidden"] is True
    assert next_workout["next_workout"]["focus"] == "recovery"
    assert next_workout["recommendation_sources"]["open_wearables"]["role"] == "bounded modifier"
    assert next_workout["recommendation_sources"]["load_source"] == "apple_health"
    assert next_workout["recommendation_sources"]["load_source_summary_hidden"] is True
    assert next_workout["recommendation_sources"]["whoop"]["summary_hidden"] is True


def test_open_wearables_facts_route_filters_current_profile(monkeypatch, tmp_path):
    module = _seed_open_wearables_fact_route_context(monkeypatch, tmp_path)
    db = module.WEARABLE_FACTS_DB_FILE
    today = date.today().isoformat()
    upsert_daily_facts(db, [
        WearableDailyFact(today, "open_wearables", "Open Wearables", "sleep_duration", 120, "min", confidence="medium", freshness="fresh", used_for_recommendation=True),
    ], profile_key="2")

    monkeypatch.setattr(module, "_current_data_user_id", lambda: 2)
    profile_two = module.app.test_client().get("/api/wearable-facts").get_json()
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    profile_one = module.app.test_client().get("/api/wearable-facts").get_json()

    assert [fact["value"] for fact in profile_two["facts"]] == [120]
    assert {fact["value"] for fact in profile_one["facts"]} == {330, 95}


def test_open_wearables_modifier_never_hardens_recommendation():
    import app as module

    recommendation, modifier = module._apply_open_wearables_recommendation_guard(
        "moderate",
        [WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 480, "min", freshness="fresh").public_dict()],
    )

    assert recommendation == "moderate"
    assert modifier["applied"] is False
