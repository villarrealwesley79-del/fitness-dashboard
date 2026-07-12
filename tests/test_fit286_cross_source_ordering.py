import copy
import importlib

from test_freshness import _freshness_payload, _stub_smart_recommendation_dependencies


def test_smart_recommendation_applies_cross_source_modifiers_once_in_order(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit286-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit286-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
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
    monkeypatch.setattr(module.workout_adaptation, "project_event", lambda event: event)
    monkeypatch.setattr(
        module,
        "generate_next_workout",
        lambda *_args, **_kwargs: {
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
        },
    )

    whoop_explanation = "WHOOP recovery 34 triggered one deload modifier"
    conflict_explanation = "Oura readiness and WHOOP recovery disagree; WHOOP stays conservative"
    monkeypatch.setattr(
        module,
        "_whoop_recommendation_context",
        lambda _readiness: {
            "signals": {
                "display_only": False,
                "applied_modifiers": ["deload"],
                "explanations": [whoop_explanation],
                "freshness": {"status": "fresh"},
            },
            "source_conflict": {
                "has_conflict": True,
                "conservative_source": "whoop",
                "explanation": conflict_explanation,
            },
        },
    )
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
        }],
    )

    accepted_food = [{
        "id": 286,
        "status": "accepted",
        "date": module._today_str(),
        "calories": 900,
        "protein_g": 20,
    }]
    monkeypatch.setattr(module, "_food_log_entries_for_context", lambda *_args, **_kwargs: accepted_food)
    adaptation_calls = []

    def apply_food_adaptation(plan, *, food_log_entries, **_kwargs):
        adaptation_calls.append([entry["id"] for entry in food_log_entries])
        adapted = copy.deepcopy(plan)
        adapted["exercises"][0]["target_sets"] = 3
        adapted["exercises"][0]["rationale"] += " · Food adaptation applied"
        return adapted, [{
            "status": "applied",
            "reason": "Accepted food signal reduced volume once",
        }]

    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", apply_food_adaptation)

    response = module.app.test_client().get("/api/recommendation/smart")

    assert response.status_code == 200
    payload = response.get_json()
    exercise = payload["next_workout"]["exercises"][0]
    assert adaptation_calls == [[286]]
    assert exercise["target_sets"] == 2  # food sets 3 first; WHOOP's 0.8 clamp runs afterward
    assert exercise["rationale"].count("Food adaptation applied") == 1
    assert exercise["rationale"].count("WHOOP modifier applied") == 1
    assert payload["workout_adaptation_events"] == [{
        "status": "applied",
        "reason": "Accepted food signal reduced volume once",
    }]

    open_wearables_detail = payload["recommendation_sources"]["open_wearables"]["detail"]
    reasoning = payload["reasoning"]
    assert reasoning.count(whoop_explanation) == 1
    assert reasoning.count(conflict_explanation) == 1
    assert reasoning.count(open_wearables_detail) == 1
    assert reasoning.index("Readiness 88") < reasoning.index(whoop_explanation)
    assert reasoning.index(whoop_explanation) < reasoning.index(conflict_explanation)
    assert reasoning.index(conflict_explanation) < reasoning.index(open_wearables_detail)
    assert list(payload["recommendation_sources"]) == [
        "load_source",
        "load_source_summary_hidden",
        "open_wearables",
        "source_conflict",
        "wearable_sources",
        "whoop",
    ]
    assert payload["recommendation_sources"]["open_wearables"]["used_for_recommendation"] is True
    assert payload["recommendation_sources"]["whoop"]["applied_modifiers"] == ["deload"]
