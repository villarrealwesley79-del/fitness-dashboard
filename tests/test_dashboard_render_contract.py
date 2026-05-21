from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _paint_body() -> str:
    """Return the body of paintDashboardFromState() so individual painter
    guards can be inspected without false positives from elsewhere in app.js.
    """
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "function paintDashboardFromState()"
    assert marker in app_js, "paintDashboardFromState not found"
    return app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]


def test_render_dashboard_preserves_fit125_repaint_chains():
    """FIT-127 must not regress FIT-125: each primary endpoint still gets its
    own .then(repaint) so a slow endpoint can't block the others."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    assert "async function renderDashboard()" in app_js
    assert ".then(repaint, () => repaint())" in app_js, (
        "FIT-125 per-slice repaint chains must remain — FIT-127 only adds "
        "per-field guards inside paintDashboardFromState, it does not change "
        "renderDashboard's fetch fan-out"
    )


def test_readiness_gauge_paint_is_guarded_on_missing_data():
    """FIT-127 AC: the readiness gauge must NOT paint a 0%/"Low" reading when
    neither oura.readiness nor dash.recomp_command.readiness is hydrated."""
    body = _paint_body()
    assert "gaugeChart($('readiness-gauge-svg')" in body, "gauge call missing"

    # The gauge call must sit inside an `if (readiness != null)` (or equivalent
    # truthiness-respecting non-zero check) so the painter skips on empty
    # state instead of falling through to `|| 0`.
    assert "if (readiness != null) {" in body, (
        "readiness gauge must be guarded by `if (readiness != null)` — a real "
        "Oura reading of 0 should still paint, but undefined/missing must skip"
    )

    # And the old unconditional `|| 0` collapse to a literal zero is gone.
    assert "readiness = (oura && oura.readiness) || (dash && dash.recomp_command && dash.recomp_command.readiness) || 0;" not in body, (
        "the old `|| 0` fallback in `readiness` is the regression source — "
        "use null sentinels per readiness source and gate the paint"
    )


def test_reco_title_assignment_is_guarded():
    """FIT-127 AC: the reco-title textContent must NOT receive the literal
    "Rest Day" fallback when neither reco nor dash.next_workout is hydrated."""
    body = _paint_body()

    # The literal "Rest Day" must no longer terminate the recoTitle fallback
    # chain — it should fall through to null instead.
    assert "|| 'Rest Day';" not in body, (
        "literal 'Rest Day' fallback must be replaced with null so the "
        "reco-title HTML placeholder ('—') stays put on cold open"
    )

    # And the textContent assignment must be guarded on a truthy recoTitle.
    assert "if (recoTitle && $('reco-title'))" in body, (
        "reco-title assignment must be wrapped in a truthy guard so the "
        "HTML placeholder is preserved when recoTitle resolves to null"
    )


def test_reco_intensity_assignment_is_guarded():
    """FIT-127 AC: the reco-intensity chip must NOT receive the literal
    "Moderate" fallback when reco.recommendation is absent."""
    body = _paint_body()

    # The `: 'Moderate'` ternary fallback must be gone.
    assert ": 'Moderate';" not in body, (
        "literal 'Moderate' fallback in intensityWord must be replaced with "
        "null so the intensity chip placeholder stays put on cold open"
    )

    # And the `|| 'Moderate'` collapse on the final textContent must be gone.
    assert "].filter(Boolean).join(' · ') || 'Moderate';" not in body, (
        "the `|| 'Moderate'` collapse on the joined intensity text must be "
        "replaced with a truthy guard around the assignment"
    )


def test_reco_why_assignment_is_guarded():
    """FIT-127 AC: the reco-why textContent must NOT receive the canned
    'Based on your readiness, sleep, and training load.' fallback when there's
    no reco.reasoning AND no wearable-stale/missing signal."""
    body = _paint_body()

    # The canned fallback must NOT be unconditionally assigned. It can still
    # appear elsewhere if the painter is rewritten, so check for the specific
    # `|| 'Based on…'` collapse that caused the regression.
    assert "|| 'Based on your readiness, sleep, and training load.';" not in body, (
        "the `|| 'Based on your readiness…'` fallback in whyText must be "
        "replaced with null so the reco-why placeholder stays put on cold open"
    )

    # And the textContent assignment + class toggle must be inside a truthy
    # guard on whyText.
    assert "if (whyText) {" in body, (
        "the whyEl.textContent assignment must be wrapped in `if (whyText)` "
        "so it skips painting when there's no real reasoning to show"
    )


def test_reco_confidence_pct_assignment_is_guarded():
    """FIT-127 AC: the reco-confidence-pct chip must NOT receive the legacy
    '45%' worst-bucket fallback when readiness is null (cold open). The HTML
    placeholder is '--%' (templates/index.html:65)."""
    body = _paint_body()

    # The legacy readiness-based ladder must be gated on `readiness != null`
    # so cold-open with no readiness data doesn't fall through to '45%'.
    assert "readiness != null ? (readiness >= 80 ?" in body, (
        "confidence ladder must be gated on `readiness != null` so cold-open "
        "doesn't paint the '45%' worst-bucket fallback"
    )

    # The textContent assignment must be wrapped in a truthy guard on confLabel.
    assert "if (confLabel && $('reco-confidence-pct'))" in body, (
        "reco-confidence-pct assignment must be wrapped in a truthy guard so "
        "the '--%' HTML placeholder is preserved when confLabel is null"
    )


def test_insight_card_paint_is_guarded_on_missing_reco():
    """FIT-127 AC: the insight card must NOT receive the canned 'Recovery is
    on track' / 'Keep your sleep consistent…' text when reco hasn't resolved.
    The HTML placeholder is 'Gathering data…' (templates/index.html:193)."""
    body = _paint_body()

    assert "// Insight card" in body, "insight card section comment missing"

    # Carve out the insight-card region: from the `// Insight card` comment
    # to the next major sub-section header (Sparkline). Both textContent
    # assignments AND a `if (reco) {` guard must appear in that region, and
    # the guard must precede the assignments — otherwise the canned strings
    # paint unguarded on cold open.
    insight_section = body.split("// Insight card", 1)[1].split("// Sparkline:", 1)[0]

    title_idx = insight_section.find("$('insight-title').textContent")
    body_idx = insight_section.find("$('insight-body').textContent")
    guard_idx = insight_section.find("if (reco) {")

    assert title_idx != -1, "insight-title textContent assignment missing"
    assert body_idx != -1, "insight-body textContent assignment missing"
    assert guard_idx != -1, (
        "`if (reco) {` guard missing from the insight-card section — the "
        "canned 'Recovery is on track' / 'Keep your sleep consistent…' text "
        "would paint over the 'Gathering data…' placeholder on cold open"
    )
    assert guard_idx < title_idx and guard_idx < body_idx, (
        "`if (reco) {` guard must precede the insight-title / insight-body "
        "textContent assignments"
    )

    # And the canned defaults must NOT sit outside the guard block. We detect
    # this by asserting the literal strings only appear AFTER the guard.
    canned_title_idx = insight_section.find("'Recovery is on track'")
    canned_body_idx = insight_section.find("'Keep your sleep consistent")
    assert canned_title_idx == -1 or canned_title_idx > guard_idx, (
        "literal 'Recovery is on track' must sit inside the `if (reco)` guard"
    )
    assert canned_body_idx == -1 or canned_body_idx > guard_idx, (
        "literal 'Keep your sleep consistent…' must sit inside the "
        "`if (reco)` guard"
    )
