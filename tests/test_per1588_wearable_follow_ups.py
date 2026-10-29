"""PER-1588 wearable follow-ups from the W4b review. Synthetic data only."""

from __future__ import annotations

import time
from datetime import date

from wearable_fact_store import WearableDailyFact, preferred_daily_facts, upsert_daily_facts

DAY = date.today().isoformat()


def _fact(provider, metric, value, *, source_id, freshness="fresh", system="open_wearables"):
    return WearableDailyFact(
        date=DAY, provider_id=provider, source_label=provider.title(), metric=metric, value=value,
        source_system=system, source_record_kind="summary", source_id=source_id, source_provider=provider,
        freshness=freshness, used_for_recommendation=True,
    )


def _read(db, *metrics):
    return preferred_daily_facts(db, set(metrics), DAY, DAY).get(DAY, {})


def test_a_live_fact_with_unknown_freshness_is_skipped_before_ranking(tmp_path):
    db = str(tmp_path / "facts.db")
    upsert_daily_facts(db, [_fact("oura", "sleep_duration", 420, source_id="valid")])
    time.sleep(0.01)  # the invalid-time row is the newest write, so it would win the ranking
    upsert_daily_facts(db, [_fact("oura", "sleep_duration", 300, source_id="invalid-time", freshness="unknown")])

    assert _read(db, "sleep_duration")["sleep_duration"]["value"] == 420


def test_an_archive_fact_with_unknown_freshness_still_counts(tmp_path):
    db = str(tmp_path / "facts.db")
    upsert_daily_facts(db, [_fact("oura", "sleep_duration", 410, source_id="archive:1", freshness="unknown", system="archive")])

    assert _read(db, "sleep_duration")["sleep_duration"]["value"] == 410


def test_a_forced_night_uses_the_one_provider_record_that_carries_the_most_sleep_metrics(tmp_path):
    db = str(tmp_path / "facts.db")
    upsert_daily_facts(db, [  # oura and whoop disagree by more than the tolerance: the shorter provider (whoop) is used
        _fact("oura", "sleep_duration", 480, source_id="o"),
        _fact("whoop", "sleep_duration", 300, source_id="full"),
        _fact("whoop", "sleep_efficiency", 90, source_id="full"),
        _fact("whoop", "sleep_avg_heart_rate", 55, source_id="full"),
    ])
    time.sleep(0.01)  # a newer, thinner whoop record would otherwise win sleep_duration
    upsert_daily_facts(db, [_fact("whoop", "sleep_duration", 310, source_id="thin")])

    night = _read(db, "sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate")

    assert {metric: fact["value"] for metric, fact in night.items()} == {
        "sleep_duration": 300, "sleep_efficiency": 90, "sleep_avg_heart_rate": 55,
    }


def test_a_forced_night_keeps_live_over_a_fuller_archive_record(tmp_path):
    db = str(tmp_path / "facts.db")
    upsert_daily_facts(db, [
        _fact("oura", "sleep_duration", 480, source_id="o"),
        _fact("whoop", "sleep_duration", 300, source_id="live"),
        _fact("whoop", "sleep_duration", 320, source_id="archive:1", system="archive", freshness="stale"),
        _fact("whoop", "sleep_efficiency", 85, source_id="archive:1", system="archive", freshness="stale"),
        _fact("whoop", "sleep_avg_heart_rate", 50, source_id="archive:1", system="archive", freshness="stale"),
    ])
    fact = _read(db, "sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate")["sleep_duration"]
    assert (fact["value"], fact["via"]) == (300, "open_wearables")


def _codex_p2_night(db, full_minutes):
    # Oura 480; WHOOP has a newer duration-only 300 record and an older fuller record carrying efficiency and heart rate.
    upsert_daily_facts(db, [
        _fact("whoop", "sleep_duration", full_minutes, source_id="full"),
        _fact("whoop", "sleep_efficiency", 90, source_id="full"),
        _fact("whoop", "sleep_avg_heart_rate", 55, source_id="full"),
        _fact("oura", "sleep_duration", 480, source_id="o"),
    ])
    time.sleep(0.01)
    upsert_daily_facts(db, [_fact("whoop", "sleep_duration", 300, source_id="thin")])


def test_the_codex_p2_night_never_reports_540(tmp_path):
    """Codex P2: whoop's representative is the 540 record, 540 vs 480 is within tolerance, so Oura (preferred) is used, unlabelled."""
    db = str(tmp_path / "facts.db")
    _codex_p2_night(db, 540)

    fact = _read(db, "sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate")["sleep_duration"]

    assert (fact["provider"], fact["value"]) == ("oura", 480)
    assert fact["conflict_state"] != "conservative_value_used"


def test_a_conservative_night_never_reports_more_than_the_competing_representative(tmp_path):
    """Codex P2: the duration that justified the conservative pick must not be re-ranked to a longer one."""
    db = str(tmp_path / "facts.db")
    _codex_p2_night(db, 600)  # whoop's representative (600) vs oura 480: a real conflict, the shorter oura night wins

    night = _read(db, "sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate")

    assert (night["sleep_duration"]["provider"], night["sleep_duration"]["value"]) == ("oura", 480)
    assert night["sleep_duration"]["conflict_state"] == "conservative_value_used"
    assert set(night) == {"sleep_duration"}  # oura's record carries nothing else: never borrowed from whoop


def test_a_forced_night_reports_efficiency_from_the_record_that_supplied_the_duration(tmp_path):
    db = str(tmp_path / "facts.db")
    upsert_daily_facts(db, [  # whoop is the shorter provider; its fullest record has no duration at all
        _fact("oura", "sleep_duration", 480, source_id="o"),
        _fact("whoop", "sleep_duration", 300, source_id="has-duration"),
        _fact("whoop", "sleep_efficiency", 88, source_id="has-duration"),
        _fact("whoop", "sleep_efficiency", 70, source_id="no-duration"),
        _fact("whoop", "sleep_avg_heart_rate", 50, source_id="no-duration"),
    ])

    night = _read(db, "sleep_duration", "sleep_efficiency", "sleep_avg_heart_rate")

    assert {m: f["value"] for m, f in night.items()} == {"sleep_duration": 300, "sleep_efficiency": 88}
