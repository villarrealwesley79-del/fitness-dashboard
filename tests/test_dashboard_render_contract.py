import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _paint_body() -> str:
    """Return the body of paintDashboardFromState() so individual painter
    guards can be inspected without false positives from elsewhere in app.js.
    """
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "function paintDashboardFromState()"
    assert marker in app_js, "paintDashboardFromState not found"
    return app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]


def _render_stats_body() -> str:
    """Return the body of renderStats() for Stats-tab contract checks."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function renderStats()"
    assert marker in app_js, "renderStats not found"
    return app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]


def _resolve_progress_insight_visuals(payloads: list[dict]) -> list[dict]:
    """Execute the production visual resolver for representative API rows."""
    if not shutil.which("node"):
        pytest.skip("FIT-322 rendered-payload contract requires Node.js")

    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "function progressInsightVisual(ins)"
    assert marker in app_js, "progressInsightVisual not found"
    helper = marker + app_js.split(marker, 1)[1].split("\n    async function renderStats()", 1)[0]
    script = f"""
const vm = require('node:vm');
const sandbox = {{}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(helper)}, sandbox);
const payloads = {json.dumps(payloads)};
process.stdout.write(JSON.stringify(payloads.map((row) => sandbox.progressInsightVisual(row))));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_render_dashboard_preserves_fit125_repaint_chains():
    """FIT-127 + FIT-129 must not regress FIT-125: each primary endpoint
    still gets its own .then chain in renderDashboard so a slow endpoint
    can't block the others. FIT-129 replaced the bare `.then(repaint, () =>
    repaint())` shape with gen-guarded settle calls, but the per-slice
    fan-out (one .then per fetcher) is still the contract."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    assert "async function renderDashboard()" in app_js
    body = app_js.split("async function renderDashboard()", 1)[1].split("\n    }\n", 1)[0]

    # Each of the four primary fetchers must have its own .then chain in
    # renderDashboard. We assert this by checking that each fetcher's
    # invocation inside renderDashboard is immediately followed by `.then(`.
    invocations = {
        "getDashboard": "getDashboard(true, isRecoCurrent).then(",
        "getOuraStatus": "getOuraStatus(true, false, isOuraCurrent).then(",
        "getReco": "getReco(true, isRecoCurrent).then(",
        "getOuraSleep": "getOuraSleep(true, isSleepCurrent).then(",
    }
    for fetcher, invocation in invocations.items():
        assert invocation in body, (
            f"FIT-125 per-slice fan-out must remain — {fetcher} must have its "
            f"own .then chain inside renderDashboard so a slow endpoint can't "
            f"block the others"
        )


def test_readiness_gauge_paint_is_guarded_on_missing_data():
    """FIT-127 AC: the readiness gauge must NOT paint a 0%/"Low" reading when
    neither oura.readiness nor dash.recomp_command.readiness is hydrated.

    Also (P1 follow-up): when readiness is null AFTER a prior paint (e.g.
    invalidateCaches() + slow/failed refetch), the SVG must be cleared so the
    prior session's ring/value doesn't linger as stale guidance — gaugeChart
    is the only writer to #readiness-gauge-svg.
    """
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

    # Clear-on-null else branch must reset the gauge container so a prior
    # session's ring doesn't linger after invalidate + slow/failed refetch.
    assert "gaugeEl.innerHTML = ''" in body, (
        "readiness gauge must clear #readiness-gauge-svg to empty when "
        "readiness is null — gaugeChart is the only other writer to the "
        "container, so skipping leaves stale SVG on screen"
    )


def test_reco_title_assignment_is_guarded():
    """FIT-127 AC: the reco-title textContent must NOT receive the literal
    "Rest Day" fallback when neither reco nor dash.next_workout is hydrated.

    Also (P1 follow-up): when recoTitle resolves to null AFTER a prior paint,
    the chip must reset to the '—' HTML placeholder so a prior session's
    title doesn't linger after invalidate + slow/failed refetch.
    """
    body = _paint_body()

    # The literal "Rest Day" must no longer terminate the recoTitle fallback
    # chain — it should fall through to null instead.
    assert "|| 'Rest Day';" not in body, (
        "literal 'Rest Day' fallback must be replaced with null so the "
        "reco-title HTML placeholder ('—') stays put on cold open"
    )

    # And the textContent assignment must be guarded on a truthy recoTitle.
    assert "if (recoTitle) {" in body, (
        "reco-title assignment must be wrapped in `if (recoTitle)` so the "
        "HTML placeholder is preserved when recoTitle resolves to null"
    )

    # Clear-on-null else branch must reset the chip to the '—' HTML placeholder.
    assert "$('reco-title').textContent = '—';" in body, (
        "reco-title must reset to '—' HTML placeholder when recoTitle is "
        "null — skipping would leave a prior session's title on screen"
    )


def test_reco_intensity_assignment_is_guarded():
    """FIT-127 AC: the reco-intensity chip must NOT receive the literal
    "Moderate" fallback when reco.recommendation is absent.

    Also (P1 follow-up): when neither focus nor reco.recommendation is
    available AFTER a prior paint, the chip must reset to the '—' HTML
    placeholder.
    """
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

    # Clear-on-null else branch must reset the chip to '—' HTML placeholder.
    assert "$('reco-intensity').textContent = '—';" in body, (
        "reco-intensity must reset to '—' HTML placeholder when intensityText "
        "is empty — skipping would leave a prior session's chip on screen"
    )


def test_reco_why_assignment_is_guarded():
    """FIT-127 AC: the reco-why textContent must NOT receive the canned
    'Based on your readiness, sleep, and training load.' fallback when there's
    no reco.reasoning AND no wearable-stale/missing signal.

    Also (P1 follow-up): when whyText resolves to null AFTER a prior paint,
    the textContent must reset to the 'Analyzing your readiness…' HTML
    placeholder AND the .lower-confidence class must be cleared so a prior
    session's reasoning doesn't linger.
    """
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

    # Clear-on-null else branch must reset the textContent to the HTML
    # placeholder AND clear the lower-confidence class.
    assert "whyEl.textContent = 'Analyzing your readiness, sleep, and training load…';" in body, (
        "reco-why must reset to the 'Analyzing your readiness…' HTML "
        "placeholder when whyText is null — skipping would leave a prior "
        "session's reasoning on screen"
    )
    assert "whyEl.classList.remove('lower-confidence');" in body, (
        "reco-why must clear the .lower-confidence class when resetting to "
        "the placeholder so a prior session's degraded styling doesn't linger"
    )


def test_reco_confidence_pct_assignment_is_guarded():
    """FIT-127 AC: the reco-confidence-pct chip must NOT receive the legacy
    '45%' worst-bucket fallback when readiness is null (cold open). The HTML
    placeholder is '--%' (templates/index.html:65).

    Also (P1 follow-up): when confLabel resolves to null AFTER a prior paint,
    the chip must reset to the '--%' HTML placeholder so a prior session's
    confidence value doesn't linger.
    """
    body = _paint_body()

    # The legacy readiness-based ladder must be gated on `readiness != null`
    # so cold-open with no readiness data doesn't fall through to '45%'.
    assert "readiness != null ? (readiness >= 80 ?" in body, (
        "confidence ladder must be gated on `readiness != null` so cold-open "
        "doesn't paint the '45%' worst-bucket fallback"
    )

    # The textContent assignment must be wrapped in a truthy guard on confLabel.
    assert "if (confLabel) {" in body, (
        "reco-confidence-pct assignment must be wrapped in `if (confLabel)` "
        "so the '--%' HTML placeholder is preserved when confLabel is null"
    )

    # Clear-on-null else branch must reset the chip to '--%' HTML placeholder.
    assert "$('reco-confidence-pct').textContent = '--%';" in body, (
        "reco-confidence-pct must reset to '--%' HTML placeholder when "
        "confLabel is null — skipping would leave a prior session's % on screen"
    )


def test_insight_card_paint_is_guarded_on_missing_reco():
    """FIT-127 AC: the insight card must NOT receive the canned 'Recovery is
    on track' / 'Keep your sleep consistent…' text when reco hasn't resolved.
    The HTML placeholder is 'Gathering data…' (templates/index.html:193).

    Also (P1 follow-up): when state.reco is null AFTER a prior paint (e.g.
    the getReco catch path or a post-invalidate refetch that's slow/fails),
    the title must reset to 'Gathering data…' and the body must clear, so a
    prior session's insight doesn't linger as stale guidance.
    """
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

    # Clear-on-null else branch must reset title to 'Gathering data…' and
    # clear body so a prior session's insight doesn't linger.
    assert "$('insight-title').textContent = 'Gathering data…';" in insight_section, (
        "insight-title must reset to 'Gathering data…' HTML placeholder when "
        "reco is null — skipping would leave a prior session's insight title "
        "on screen as stale guidance"
    )
    assert "$('insight-body').textContent = '';" in insight_section, (
        "insight-body must clear to empty when reco is null — skipping would "
        "leave a prior session's reasoning visible"
    )


def test_stats_insights_empty_state_resets_to_placeholder():
    """FIT-148 AC: Stats Progress Insights must reset to the empty-state
    placeholder when /api/insights returns no usable insight rows.
    """
    body = _render_stats_body()
    section = body.split("// Insights list", 1)[1]

    clear_idx = section.find("list.innerHTML = '';")
    items_idx = section.find("const items = Array.isArray")
    empty_idx = section.find("Log a few workouts to start tracking progress.")
    append_idx = section.find("list.appendChild(card);")

    assert clear_idx != -1, "Stats insights list must clear before repainting"
    assert items_idx != -1, "Stats insights must guard null/non-array payloads with Array.isArray"
    assert empty_idx != -1, "Stats insights empty-state copy must match FIT-148"
    assert append_idx != -1, "Stats insight card append path missing"
    assert clear_idx < items_idx < empty_idx < append_idx, (
        "Stats insights must clear stale cards, normalize items, then render the "
        "empty placeholder before the card-append branch"
    )


def test_stats_insight_visuals_cover_backend_types_and_semantic_icons():
    """FIT-322: tone comes from type, while the glyph keeps payload meaning."""
    visuals = _resolve_progress_insight_visuals([
        {"type": "positive", "icon": "trending_up"},
        {"type": "warning", "icon": "pause"},
        {"type": "negative", "icon": "trending_down"},
        {"type": "info", "icon": "fitness_center"},
        {"type": "negative", "icon": "warning"},
        {"type": "danger"},
    ])

    assert visuals == [
        {"iconClass": "pos", "iconChar": "↑"},
        {"iconClass": "warn", "iconChar": "‖"},
        {"iconClass": "neg", "iconChar": "↓"},
        {"iconClass": "info", "iconChar": "i"},
        {"iconClass": "neg", "iconChar": "!"},
        {"iconClass": "neg", "iconChar": "▲"},
    ]


def test_dashboard_whoop_source_contract_is_wired_into_reco_card():
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    html = (ROOT / "templates" / "index.html").read_text()

    assert "reco-fresh-whoop" in html
    assert "btn-reco-sources" in html
    assert "modal-reco-sources" in html
    assert "function formatWhoopChip(whoop, ago)" in app_js
    assert "key: 'whoop'" in app_js
    assert "renderRecommendationSourceSummary(dash, reco, freshnessWithWhoop);" in app_js
    assert "mergeWhoopFreshnessNode(" in app_js


def test_dashboard_whoop_state_matrix_is_explicit_in_frontend_contract():
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    for token in [
        "connected",
        "disconnected",
        "syncing",
        "fresh",
        "aging",
        "stale",
        "pending_score",
        "unscorable",
        "calibrating",
        "reauth_required",
        "csv_only",
        "source_conflict",
        "error",
        "WHOOP · no data",
    ]:
        assert token in app_js, f"WHOOP UI state token {token!r} missing from app.js"
