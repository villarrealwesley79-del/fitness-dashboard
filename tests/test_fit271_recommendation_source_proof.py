from recommendation_sources import build_open_wearables_recommendation_source, build_recommendation_source_proof

RECOVERY_NODE = {"provider": "oura", "via": "open_wearables", "date": "2026-10-28", "freshness": "fresh", "conflict_state": None}


def _freshness(**overrides):
    base = {
        "open_wearables": {"status": "fresh"},
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
            "domain_sources": {"recovery": RECOVERY_NODE},
            "domain_fields": {"recovery": ["readiness_score"]},
            "domain_modifier_applied": {"sleep": True, "recovery": True, "activity": True},
            "weather_fields": ["feelslike_f"],
            "weather_status": "fresh",
            "weather_modifier_applied": True,
            **(source_inputs or {}),
        },
    )


def test_source_proof_has_the_domain_contract():
    proof = _proof()

    assert set(proof) == {"open_wearables", "sleep", "recovery", "activity", "weather"}
    for domain in ("sleep", "recovery", "activity"):
        assert set(proof[domain]) == {
            "provider", "via", "date", "freshness", "used_for_recommendation", "fields_used",
            "modifier_applied", "ignored_reason", "conflict_state",
        }
    for source in ("open_wearables", "weather"):
        assert set(proof[source]) == {
            "used_for_recommendation", "fields_used", "modifier_applied", "ignored_reason"
        }
    assert proof["open_wearables"] == {
        "used_for_recommendation": True,
        "fields_used": ["sleep_duration"],
        "modifier_applied": True,
        "ignored_reason": None,
    }
    assert proof["recovery"] == {
        **RECOVERY_NODE,
        "used_for_recommendation": True,
        "fields_used": ["readiness_score"],
        "modifier_applied": True,
        "ignored_reason": None,
    }
    assert proof["sleep"]["ignored_reason"] == "missing_data" and proof["sleep"]["provider"] is None
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


def test_stale_domain_is_ignored_with_no_fields_or_modifier():
    proof = _proof(source_inputs={"domain_sources": {"recovery": {**RECOVERY_NODE, "freshness": "stale"}}})

    assert proof["recovery"]["used_for_recommendation"] is False
    assert proof["recovery"]["fields_used"] == []
    assert proof["recovery"]["modifier_applied"] is False
    assert proof["recovery"]["ignored_reason"] == "stale_source"
    assert proof["recovery"]["provider"] == "oura"


def test_archive_sourced_domain_names_the_archive():
    proof = _proof(source_inputs={"domain_sources": {"recovery": {**RECOVERY_NODE, "via": "archive"}}})

    assert proof["recovery"]["via"] == "archive"


def test_whoop_recovery_is_named_only_as_an_open_wearables_field():
    proof = _proof(source_inputs={
        "open_wearables_facts": [{"metric": "recovery_score", "provider_id": "whoop", "value": 55}],
    })

    assert "whoop" not in proof
    assert proof["open_wearables"]["fields_used"] == ["recovery_score"]
    assert proof["open_wearables"]["modifier_applied"] is True


def test_out_of_range_or_boolean_whoop_scores_are_never_reported_as_used():
    for bad in (True, 150, -1, "high", None):
        proof = _proof(source_inputs={
            "open_wearables_facts": [{"metric": "recovery_score", "provider_id": "whoop", "value": bad}],
        })
        assert proof["open_wearables"]["fields_used"] == [], bad
        assert proof["open_wearables"]["used_for_recommendation"] is False, bad
        source = build_open_wearables_recommendation_source(
            {"status": "fresh"},
            facts=[{"metric": "recovery_score", "provider_id": "whoop", "value": bad}],
        )
        assert source["used_for_recommendation"] is False and source["facts_used"] == 0, bad


def test_open_wearables_detail_counts_only_facts_that_feed_the_recommendation():
    facts = [
        {"metric": "steps", "value": 9000},
        {"metric": "sleep_duration", "value": 400},
        {"metric": "recovery_score", "provider_id": "whoop", "value": 60},
    ]
    source = build_open_wearables_recommendation_source({"status": "fresh"}, facts=facts)
    assert source["facts_used"] == 2
    assert "2 normalized facts" in source["detail"]

    nothing = build_open_wearables_recommendation_source(
        {"status": "fresh"}, facts=[{"metric": "steps", "value": 9000}, {"metric": "resting_heart_rate", "value": 50}],
    )
    assert nothing["facts_used"] == 0
    assert nothing["detail"] == "Open Wearables has 2 facts; none feed today's recommendation."


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
        {"domain_sources": {"recovery": RECOVERY_NODE}, "domain_fields": {"recovery": ["readiness_score"]}},
    )

    assert "open_wearables" in payload
    assert "whoop" not in payload
    assert set(payload["source_proof"]) == {"open_wearables", "sleep", "recovery", "activity", "weather"}


def test_wearable_rule_tracks_rules_even_when_their_net_category_cancels():
    import app as module

    assert any(module._wearable_factor_rules(
        90,
        {"bonus_points": 0},
        "declining",
        {"debt_minutes": 0},
    ).values()) is True


def test_wearable_rule_does_not_claim_a_local_recovery_bonus_threshold_change():
    import app as module

    assert any(module._wearable_factor_rules(
        80,
        {"bonus_points": 10},
        "stable",
        {"debt_minutes": 0},
    ).values()) is False


def test_wearable_rule_uses_effective_readiness_for_recovery_threshold():
    import app as module

    assert any(module._wearable_factor_rules(
        65,
        {"bonus_points": 10},
        "stable",
        {"debt_minutes": 0},
    ).values()) is False
