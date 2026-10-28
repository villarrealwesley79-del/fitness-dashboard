"""Reviewer test for PR #328: adding Open Wearables must not change how many sessions history counts."""

from __future__ import annotations

from js_runtime import run_app_js


def test_one_strength_session_is_counted_once_with_or_without_open_wearables():
    counts = run_app_js(
        [
            "normalizeWatchHistoryRow",
            "normalizeOpenWearablesHistoryRow",
            "mergeOpenWearablesHistory",
            "mergeStrengthHistorySources",
        ],
        """
const lift = { id: 'lift-1', date: '2026-07-12', source: 'lifted' };
const watch = e.normalizeWatchHistoryRow({ id: 'aw-1', date: '2026-07-12', activity_type: 'Traditional Strength Training',
  start: '2026-07-12T07:00:00-05:00' });
const ow = e.normalizeOpenWearablesHistoryRow({ id: 'open_wearables:w1', date: '2026-07-12', activity_type: 'strength_training',
  session_type: 'strength_training', start_time: '2026-07-12T12:00:00Z', calories_burned: 320, avg_heart_rate: 120, max_heart_rate: 160 });
// what renderHistory does: totalSessions = mergeStrengthHistorySources(lifts, mergeOpenWearablesHistory(watch, ow)).length
const count = (watchRows, owRows) => e.mergeStrengthHistorySources([lift], e.mergeOpenWearablesHistory(watchRows, owRows)).length;
process.stdout.write(JSON.stringify({
  withoutOw: count([watch], []),
  withOw: count([watch], [ow]),
  owOnly: count([], [ow]),
}));
""",
    )
    assert counts["withoutOw"] == 1
    # Same Watch session, now also reported by Open Wearables: still one session.
    assert counts["withOw"] == 1, counts
    # After the straight cutover the Watch session only arrives through Open Wearables.
    assert counts["owOnly"] == 1, counts
