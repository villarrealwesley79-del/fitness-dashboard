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
    assert modifier["rpe_cap"] is None
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
