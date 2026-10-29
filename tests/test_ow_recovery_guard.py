from datetime import datetime, timedelta, timezone

import open_wearables_hub as hub


def _recovery(score, provider="whoop", date="2026-10-28"):
    return {"metric": "recovery_score", "provider_id": provider, "source_provider": provider, "date": date, "value": score}


def _guard(facts, recommendation="intensity"):
    return hub.apply_recommendation_guard(
        recommendation, facts, downgrade_once=lambda r: {"intensity": "moderate", "moderate": "recovery"}.get(r, r)
    )


def test_low_whoop_recovery_downgrades_once_and_is_labelled_whoop_via_open_wearables():
    recommendation, modifier = _guard([_recovery(20)])

    assert recommendation == "moderate"
    assert modifier["applied_modifiers"] == ["recovery_deload"]
    assert modifier["rpe_cap"] == hub.RECOVERY_RPE_CAP
    assert "(provider: whoop, via: open_wearables)" in modifier["detail"]
    assert "oura" not in modifier["detail"].lower()


def test_low_recovery_and_short_sleep_still_downgrade_only_once():
    sleep = {"metric": "sleep_duration", "provider_id": "oura", "value": 300}

    recommendation, modifier = _guard([_recovery(20), sleep])

    assert recommendation == "moderate"
    assert modifier["applied_modifiers"] == ["sleep_caution", "recovery_deload"]


def test_mid_whoop_recovery_caps_rpe_without_downgrading():
    for score in (34, 50, 66):
        recommendation, modifier = _guard([_recovery(score)])
        assert recommendation == "intensity", score
        assert modifier["applied_modifiers"] == ["recovery_caution"], score
        assert modifier["rpe_cap"] == hub.RECOVERY_RPE_CAP, score


def test_good_or_missing_recovery_changes_nothing():
    for facts in ([_recovery(67)], [_recovery(70)], [], None):
        recommendation, modifier = _guard(facts)
        assert recommendation == "intensity"
        assert modifier["applied"] is False
        assert modifier["rpe_cap"] is None


def test_only_whoop_recovery_counts_and_only_the_latest_date():
    assert _guard([_recovery(20, provider="oura")])[1]["applied"] is False
    assert _guard([_recovery(20, date="2026-10-27"), _recovery(80, date="2026-10-28")])[1]["applied"] is False
    assert _guard([_recovery(80, date="2026-10-27"), _recovery(20, date="2026-10-28")])[1]["applied"] is True


def test_same_date_duplicates_use_the_lower_score():
    legacy = {**_recovery(80), "provider_id": "open_wearables"}
    assert _guard([legacy, _recovery(20)])[1]["applied_modifiers"] == ["recovery_deload"]


def test_unparseable_recovery_value_is_ignored():
    assert _guard([_recovery("n/a")])[1]["applied"] is False


def test_rpe_cap_never_loosens_as_recovery_drops():
    caps = {score: _guard([_recovery(score)])[1]["rpe_cap"] for score in (20, 33, 34, 66, 67)}
    assert caps[67] is None
    assert all(caps[score] == hub.RECOVERY_RPE_CAP for score in (20, 33, 34, 66)), caps


def test_out_of_range_recovery_scores_are_excluded_not_clamped():
    for bad in (-1, 101, 250):
        assert _guard([_recovery(bad)])[1]["applied"] is False, bad
    # an out-of-range newer score is skipped, the in-range one for the same date still counts
    assert _guard([_recovery(-1), _recovery(50)])[1]["applied_modifiers"] == ["recovery_caution"]


def test_ingest_drops_out_of_range_recovery_scores(tmp_path):
    today = datetime.now().date()
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    count = hub.store_wearable_facts(
        {"fetched_at": datetime.now(timezone.utc).isoformat(), "recovery_summary": {"data": [
            {"date": today.isoformat(), "recovery_score": -1, "source": {"provider": "whoop"}},
            {"date": (today - timedelta(days=1)).isoformat(), "recovery_score": 55, "source": {"provider": "whoop"}},
        ]}},
        db_file=db_file, profile_key="p", activity_extractor=lambda _p: [],
        sleep_extractor=lambda _p: None,
    )
    assert count == 1
    facts = hub.recommendation_facts(db_file, "p")
    assert [(f["metric"], f["value"]) for f in facts] == [("recovery_score", 55.0)]


def test_proof_counts_recovery_score_only_when_whoop_attributed():
    from recommendation_sources import build_recommendation_source_proof

    def proof_for(provider):
        facts = [_recovery(20, provider=provider)]
        modifier = hub.conservative_modifier(facts)
        source = {"used_for_recommendation": True, "freshness": "fresh"}
        return build_recommendation_source_proof(
            freshness={}, open_wearables_source=source,
            source_inputs={"open_wearables_facts": facts, "open_wearables_modifier_applied": modifier["applied"]},
        )["open_wearables"]

    assert proof_for("whoop")["fields_used"] == ["recovery_score"]
    oura = proof_for("oura")
    assert oura["fields_used"] == [] and oura["used_for_recommendation"] is False


def test_ingest_drops_boolean_recovery_scores(tmp_path):
    db_file = str(tmp_path / "wearable_facts.sqlite3")
    count = hub.store_wearable_facts(
        {"fetched_at": datetime.now(timezone.utc).isoformat(), "recovery_summary": {"data": [
            {"date": datetime.now().date().isoformat(), "recovery_score": True, "source": {"provider": "whoop"}},
        ]}},
        db_file=db_file, profile_key="p", activity_extractor=lambda _p: [],
        sleep_extractor=lambda _p: None,
    )
    assert count == 0
    assert hub.recommendation_facts(db_file, "p") == []
