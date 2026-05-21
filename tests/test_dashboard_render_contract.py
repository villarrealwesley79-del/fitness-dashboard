from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_render_dashboard_guards_cold_open_paint():
    """FIT-127: renderDashboard() must not call paintDashboardFromState() on a
    cold open. When every state slice is undefined the painters inject
    misleading defaults (0% "Low" gauge, "Rest Day" reco title, canned
    reasoning) instead of leaving the HTML placeholders alone.

    The guard requires at least one of state.dashboard / .oura / .reco /
    .ouraSleep to be hydrated before the initial paint fires. The four
    .then(repaint) chains downstream handle the per-slice paints as each
    fetch settles, so the guard does not affect the FIT-125 happy path.
    """
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    # The single point of regression is the body of renderDashboard, which
    # historically called paintDashboardFromState() unconditionally. Find that
    # body and assert the guard wraps the call.
    marker = "async function renderDashboard()"
    assert marker in app_js, "renderDashboard not found"
    body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

    assert "paintDashboardFromState();" in body, "paintDashboardFromState call missing"

    assert (
        "state.dashboard || state.oura || state.reco || state.ouraSleep"
        in body
    ), "cold-open guard missing — every-slice-undefined check must precede the first paint"

    # Make sure the guard sits BEFORE the paint call, not after.
    guard_idx = body.index("state.dashboard || state.oura || state.reco || state.ouraSleep")
    paint_idx = body.index("paintDashboardFromState();")
    assert guard_idx < paint_idx, (
        "cold-open guard must precede paintDashboardFromState() — otherwise "
        "the first paint still fires with empty state"
    )

    # And the .then(repaint) chains stay intact so per-slice paints still
    # happen as each fetch settles.
    assert ".then(repaint, () => repaint())" in app_js, (
        "FIT-125 per-slice repaint chains must remain — guard only skips the "
        "cold-open paint, not the downstream paints"
    )
