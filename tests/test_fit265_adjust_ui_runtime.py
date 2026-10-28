"""FIT-265: an 'unchanged' adjust result must show why nothing changed."""

from js_runtime import run_app_js

_SETUP = """
['adjust-state', 'adjust-result', 'adjust-summary', 'adjust-notes', 'adjust-meta'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', innerHTML: '', className: '', hidden: true };
});
sandbox.__fitSet.renderNextWorkout(() => {});
sandbox.__fitSet.renderAdjustedPlanPreview(() => {});
"""


def _notes_html(payload_js: str) -> str:
    return run_app_js(
        ["renderAdjustResult", "state"],
        _SETUP + f"""
e.renderAdjustResult({payload_js});
process.stdout.write(JSON.stringify(sandbox.elements['adjust-notes'].innerHTML));
""",
        mocks=["renderNextWorkout", "renderAdjustedPlanPreview"],
    )


def test_unchanged_adjust_shows_skipped_reason_not_safety_rail_clamp():
    html = _notes_html(
        "{ result_kind: 'unchanged', applied_notes: [], "
        "skipped_notes: [\"could not locate 'Leg Press' in current plan\"] }"
    )

    assert "could not locate &#39;Leg Press&#39; in current plan" in html or "could not locate 'Leg Press' in current plan" in html
    assert "Safety rails clamped" not in html


def test_unchanged_adjust_without_reasons_does_not_claim_a_clamp():
    html = _notes_html("{ result_kind: 'unchanged', applied_notes: [], skipped_notes: [] }")

    assert "Safety rails clamped" not in html
    assert "No change was applied" in html
