import copy
import importlib

from test_freshness import _freshness_payload, _stub_smart_recommendation_dependencies


def test_smart_recommendation_applies_cross_source_modifiers_once_in_order(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit286-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit286-health-token")
    module = importlib.import_module("app")
    monkeypatch.setitem(module.app.config, "TESTING", True)
    monkeypatch.setitem(module.app.config, "LOGIN_DISABLED", True)
    _stub_smart_recommendation_dependencies(
        monkeypatch,
        module,
        oura_row={"readiness_score": 88, "sleep_score": 82, "hrv": 44},
    )
    monkeypatch.setattr(module, "_compute_data_freshness", lambda: {
        **_freshness_payload(),
        "open_wearables": {"status": "fresh"},
    })
    monkeypatch.setattr(module, "_cached_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(module, "_apple_health_hr_intensity_summary", lambda **_kwargs: {})
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda _fingerprint: None)
    monkeypatch.setattr(module, "get_current_workout_plan", lambda _user_id: None)
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fit286-fingerprint")
    monkeypatch.setattr(module, "_workout_with_auth_scope", lambda workout: workout)
    persisted_plans = []

    def persist_plan(plan, _fingerprint):
        persisted_plans.append(copy.deepcopy(plan))
        return plan

    monkeypatch.setattr(module, "_persist_current_workout_plan", persist_plan)
    generated_recommendations = []
    modifier_sequence = []

    def generate_workout(*_args, training_recommendation=None, **_kwargs):
        modifier_sequence.append("generate")
        generated_recommendations.append(training_recommendation)
        return {
            "title": "Cross-source fixture",
            "estimated_minutes": 60,
            "estimated_duration": "60 min",
            "mesocycle": {"volume_multiplier": 1.0},
            "exercises": [{
                "exercise": "Fixture Press",
                "target_sets": 5,
                "rpe_target": 8,
                "rationale": "Base plan",
            }],
        }

    monkeypatch.setattr(module, "generate_next_workout", generate_workout)

    monkeypatch.setattr(
        module,
        "_open_wearables_recommendation_facts",
        lambda: [{
            "provider_id": "open_wearables",
            "provider_label": "Open Wearables",
            "metric": "sleep_duration",
            "value": 330,
            "unit": "min",
            "freshness": "fresh",
            "used_for_recommendation": True,
        }, {
            "provider_id": "whoop",
            "source_provider": "whoop",
            "metric": "recovery_score",
            "date": "2026-10-28",
            "value": 20,
            "freshness": "fresh",
            "used_for_recommendation": True,
        }],
    )
    original_open_wearables_guard = module._apply_open_wearables_recommendation_guard
    open_wearables_guard_calls = []

    def track_open_wearables_guard(recommendation, facts):
        modifier_sequence.append("open_wearables")
        result = original_open_wearables_guard(recommendation, facts)
        open_wearables_guard_calls.append({
            "input": recommendation,
            "output": result[0],
            "modifier": result[1],
        })
        return result

    monkeypatch.setattr(
        module,
        "_apply_open_wearables_recommendation_guard",
        track_open_wearables_guard,
    )

    response = module.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    exercise = payload["next_workout"]["exercises"][0]
    assert persisted_plans == []  # GET is read-only
    assert exercise["target_sets"] == 5  # no WHOOP set clamp any more
    assert exercise["rpe_target"] == 7  # recovery 20 also gets the RPE cap: worse recovery never shows harder targets

    open_wearables_detail = payload["recommendation_sources"]["open_wearables"]["detail"]
    # Sleep and recovery both fire, but the recommendation is downgraded once.
    assert payload["recommendation_sources"]["open_wearables"]["applied_modifiers"] == [
        "sleep_caution",
        "recovery_deload",
    ]
    assert len(open_wearables_guard_calls) == 1
    assert open_wearables_guard_calls[0]["modifier"]["downgrade"] is True
    assert generated_recommendations == [open_wearables_guard_calls[0]["output"]]
    assert open_wearables_guard_calls[0]["output"] == module._downgrade_training_recommendation_once(
        open_wearables_guard_calls[0]["input"]
    )
    assert modifier_sequence.index("open_wearables") < modifier_sequence.index("generate")
    assert "330 min" in open_wearables_detail
    assert "recovery score 20 (provider: whoop, via: open_wearables)" in open_wearables_detail
    reasoning = payload["reasoning"]
    assert reasoning.count(open_wearables_detail) == 1
    assert reasoning.index("Readiness 88") < reasoning.index(open_wearables_detail)
    assert list(payload["recommendation_sources"]) == [
        "load_source",
        "load_source_summary_hidden",
        "open_wearables",
        "source_proof",
        "wearable_sources",
    ]
    assert payload["recommendation_sources"]["open_wearables"]["used_for_recommendation"] is True
    proof = payload["recommendation_sources"]["source_proof"]["open_wearables"]
    assert proof["modifier_applied"] is True
    assert "recovery_score" in proof["fields_used"]
