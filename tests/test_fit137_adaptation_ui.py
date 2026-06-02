from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "templates" / "index.html"
STYLE_CSS = ROOT / "static" / "css" / "style.css"


def _block(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def test_adaptation_notice_uses_backend_event_feed_and_ack_endpoint():
    js = APP_JS.read_text()

    # Reads FIT-136's frozen contract feed and acks like the FIT-139 notice;
    # never invents a client-side adaptation.
    assert "/api/workout-adaptation-events?unacknowledged=true&limit=10" in js
    assert "/api/workout-adaptation-events/${encodeURIComponent(event.id)}/ack" in js
    assert "const workoutAdaptationNoticeState = {" in js
    assert "seen: new Set()" in js


def test_adaptation_notice_gates_on_applied_change_and_today():
    js = APP_JS.read_text()
    gate = _block(
        js,
        "function workoutAdaptationIsRenderable(event)",
        "function workoutAdaptationSignalLabels",
    )

    # AC1/AC8: silent (no-change / low-confidence) renders nothing; only an
    # applied change to today's plan confirms. AC5: next-day never toasts here.
    assert "if (event.silent) return false;" in gate
    assert "if (event.status !== 'applied') return false;" in gate
    assert "if (event.change_type === 'none') return false;" in gate
    assert "if (event.applies_to !== 'today') return false;" in gate


def test_adaptation_fetch_swallows_silent_and_nextday_events():
    js = APP_JS.read_text()
    fetch_block = _block(
        js,
        "async function fetchWorkoutAdaptationNotices()",
        "function newWorkoutId",
    )

    # Non-renderable events are marked seen but never shown — no empty card.
    assert "workoutAdaptationNoticeState.seen.add(event.id)" in fetch_block
    assert "if (!workoutAdaptationIsRenderable(event)) continue;" in fetch_block
    assert "showWorkoutAdaptationNotice(event)" in fetch_block


def test_adaptation_notice_renders_neutral_reason_and_collapsed_details():
    js = APP_JS.read_text()
    notice = _block(
        js,
        "function showWorkoutAdaptationNotice(event)",
        "async function fetchWorkoutAdaptationNotices",
    )

    # AC2: concise neutral reason string straight from FIT-136.
    assert "event.reason" in notice
    # AC3: per-meal/item specifics behind a collapsed native <details>.
    assert "document.createElement('details')" in notice
    assert "workout-adaptation-details" in notice
    assert "View details" in notice
    # Neutral signal labels (not moral labels) surface inside the disclosure.
    assert "workoutAdaptationSignalLabels(event)" in notice
    # Accessible, passive confirmation (mirror FIT-139 tone).
    assert "role', 'status'" in notice
    assert "aria-live', 'polite'" in notice
    assert "workout-adaptation-dismiss" in notice


def test_adaptation_does_not_surface_audit_log():
    js = APP_JS.read_text()
    block = _block(
        js,
        "const workoutAdaptationNoticeState = {",
        "function newWorkoutId",
    )

    # AC: the internal audit history is backend-only — the visible render path
    # must not fetch or render the audit-only event fields (reason_metadata /
    # rules / citations) or hit any audit endpoint.
    assert "reason_metadata" not in block
    assert "citations" not in block
    assert ".rules" not in block
    assert "/audit" not in block
    assert "audit-log" not in block
    assert "audit_log" not in block


def test_adaptation_preserves_completed_active_work_via_identity_merge():
    js = APP_JS.read_text()
    apply_block = _block(
        js,
        "function applyWorkoutAdaptationToActiveWorkout(event)",
        "function showWorkoutAdaptationNotice",
    )

    # AC: only patch when a workout is active and the change applied live;
    # reuse the FIT-179 identity-merge so completed sets survive.
    assert "if (!state.activeWorkout) return;" in apply_block
    assert "event.active_workout && event.active_workout.updated_live" in apply_block
    assert "applyAdjustedRecommendationToActiveWorkout(nw, previous)" in apply_block
    assert "renderActiveWorkout()" in apply_block


def test_adaptation_fetch_is_hooked_to_dashboard_surfaces():
    js = APP_JS.read_text()

    # Polled from the same passive surfaces as the FIT-139 refresh notice.
    assert js.count("fetchWorkoutAdaptationNotices().catch") >= 3


def test_adaptation_host_lives_in_dashboard_tab():
    html = INDEX_HTML.read_text()
    # Host is inside the Dash tab panel (appears before the next tab section).
    assert 'id="workout-adaptation-host"' in html
    host_index = html.index('id="workout-adaptation-host"')
    dash_index = html.index('id="tab-dashboard"')
    assert dash_index < host_index
    next_tab_index = html.index('id="tab-workout"') if 'id="tab-workout"' in html else len(html)
    assert host_index < next_tab_index


def test_adaptation_styles_present_and_calm():
    css = STYLE_CSS.read_text()
    block = _block(css, ".workout-adaptation-host {", ".analyze-section {")

    assert ".workout-adaptation-card {" in block
    assert ".workout-adaptation-reason {" in block
    assert ".workout-adaptation-details {" in block
    assert ".workout-adaptation-chip {" in block
    assert "overflow-wrap: anywhere" in block
