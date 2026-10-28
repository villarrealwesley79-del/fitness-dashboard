from recommendation_sources import build_recommendation_source_proof


def _freshness(**overrides):
    base = {
        "open_wearables": {"status": "fresh"},
        "oura": {"status": "fresh"},
        "apple_health": {"status": "fresh"},
    }
    base.update(overrides)
    return base


def _proof(*, freshness=None, source_inputs=None):
    return build_recommendation_source_proof(
        freshness=freshness or _freshness(),
        open_wearables_source={
            "freshness": "fresh",
            "used_for_recommendation": True,
        },
        source_inputs={
            "open_wearables_facts": [{"metric": "sleep_duration"}],
            "open_wearables_modifier_applied": True,
            "oura_fields": ["readiness_score"],
            "oura_modifier_applied": True,
            "apple_health_fields": ["recent_workout_load"],
            "weather_fields": ["feelslike_f"],
            "weather_status": "fresh",
            "weather_modifier_applied": True,
            **(source_inputs or {}),
        },
    )


def test_source_proof_has_complete_four_source_contract():
    proof = _proof()

    assert set(proof) == {"open_wearables", "oura", "apple_health", "weather"}
    for source in proof.values():
        assert set(source) == {
            "used_for_recommendation", "fields_used", "modifier_applied", "ignored_reason"
        }
    assert proof["open_wearables"] == {
        "used_for_recommendation": True,
        "fields_used": ["sleep_duration"],
        "modifier_applied": True,
        "ignored_reason": None,
    }
    assert proof["weather"]["modifier_applied"] is True


def test_open_wearables_display_metrics_are_not_claimed_as_inputs():
    proof = _proof(source_inputs={
        "open_wearables_facts": [
            {"metric": "steps"},
            {"metric": "resting_heart_rate"},
        ],
        "open_wearables_modifier_applied": False,
    })

    assert proof["open_wearables"]["used_for_recommendation"] is False
    assert proof["open_wearables"]["fields_used"] == []


def test_stale_source_is_ignored_with_no_fields_or_modifier():
    proof = _proof(freshness=_freshness(oura={"status": "stale"}))

    assert proof["oura"] == {
        "used_for_recommendation": False,
        "fields_used": [],
        "modifier_applied": False,
        "ignored_reason": "stale_source",
    }


def test_superseded_oura_names_open_wearables_as_ignored_reason():
    proof = _proof(
        freshness=_freshness(oura={
            "status": "fresh",
            "superseded_by": "open_wearables",
        }),
        source_inputs={"oura_fields": [], "oura_modifier_applied": False},
    )

    assert proof["oura"]["used_for_recommendation"] is False
    assert proof["oura"]["ignored_reason"] == "superseded_by_open_wearables"


def test_stale_apple_health_is_reported_used_when_legacy_load_logic_consumed_it():
    proof = _proof(
        freshness=_freshness(apple_health={"status": "stale"}),
        source_inputs={
            "apple_health_fields": ["recent_workout_load"],
            "apple_health_used_override": True,
        },
    )

    assert proof["apple_health"]["used_for_recommendation"] is True
    assert proof["apple_health"]["fields_used"] == ["recent_workout_load"]
    assert proof["apple_health"]["ignored_reason"] is None


def test_whoop_recovery_is_named_only_as_an_open_wearables_field():
    proof = _proof(source_inputs={
        "open_wearables_facts": [{"metric": "recovery_score", "provider_id": "whoop"}],
    })

    assert "whoop" not in proof
    assert proof["open_wearables"]["fields_used"] == ["recovery_score"]
    assert proof["open_wearables"]["modifier_applied"] is True


def test_payload_builder_exposes_source_proof_without_removing_existing_contract(monkeypatch):
    import app as module

    monkeypatch.setattr(
        module,
        "build_open_wearables_recommendation_source",
        lambda *_args, **_kwargs: {
            "freshness": "fresh",
            "used_for_recommendation": True,
        },
    )
    payload = module._recommendation_sources_payload(
        _freshness(),
        [{"metric": "sleep_duration"}],
        {"applied": True},
        {"oura_fields": ["readiness_score"]},
    )

    assert "open_wearables" in payload
    assert "whoop" not in payload
    assert set(payload["source_proof"]) == {"open_wearables", "oura", "apple_health", "weather"}


def test_oura_modifier_tracks_rules_even_when_their_net_category_cancels():
    import app as module

    assert module._oura_modifier_rule_applied(
        90,
        {"bonus_points": 0},
        "declining",
        {"debt_minutes": 0},
    ) is True


def test_oura_modifier_does_not_claim_a_local_recovery_bonus_threshold_change():
    import app as module

    assert module._oura_modifier_rule_applied(
        80,
        {"bonus_points": 10},
        "stable",
        {"debt_minutes": 0},
    ) is False


def test_oura_modifier_uses_effective_readiness_for_recovery_threshold():
    import app as module

    assert module._oura_modifier_rule_applied(
        65,
        {"bonus_points": 10},
        "stable",
        {"debt_minutes": 0},
    ) is False
