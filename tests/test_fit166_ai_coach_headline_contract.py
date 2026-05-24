"""FIT-166: AI coach card surfaces ASUS-primary / Mac-Studio-fallback.

The Settings panel previously only labelled rows as "Primary" / "Fallback".
FIT-166 adds a card-level headline plus host-name spans so the operator
can tell at a glance whether the ASUS GX10 is serving traffic, the Mac
Studio has taken over, or both hosts are down.

These tests are *static contracts* against the markup and the JS
render functions. We do not boot a browser here — the runbook covers
that with mocked screenshots. The goal is just to lock in:

  * the new IDs exist
  * the pre-existing FIT-15 / FIT-111 IDs are still present
  * the three required headline states have their friendly host
    names wired in app.js (`ASUS GX10`, `Mac Studio`)
  * fallback-active uses warn styling and fully-down uses stale
    styling (acceptance criterion).
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()


# ── New FIT-166 IDs ──────────────────────────────────────────────

def test_headline_ids_present():
    """Card-level headline introduces a small set of new IDs that
    `_renderAiCoachHeadline` writes to. Missing any of them would
    leave the headline half-painted."""
    for new_id in (
        'id="ai-coach-headline-row"',
        'id="ai-coach-headline-state"',
        'id="ai-coach-headline-host"',
        'id="ai-coach-headline-chip"',
        'id="ai-coach-headline-dot"',
        'id="ai-coach-headline-detail"',
        'id="ai-primary-host"',
        'id="ai-fallback-host"',
    ):
        assert new_id in INDEX_HTML, f"FIT-166 introduced {new_id}; markup is missing it"


# ── Pre-existing IDs preserved (FIT-15 + FIT-111) ────────────────

def test_existing_ai_coach_ids_preserved():
    """FIT-111 mandates that pre-existing element IDs survive any
    Settings refactor so the freshness/group summaries keep working."""
    for legacy_id in (
        'id="ai-coach-warning-row"',
        'id="ai-coach-warning-detail"',
        'id="ai-coach-warning-pct"',
        'id="ai-primary-dot"',
        'id="ai-primary-detail"',
        'id="ai-primary-state"',
        'id="ai-fallback-dot"',
        'id="ai-fallback-detail"',
        'id="ai-fallback-state"',
        'id="ai-metrics-detail"',
        'id="ai-metrics-fallback-pct"',
    ):
        assert legacy_id in INDEX_HTML, (
            f"FIT-166 must preserve {legacy_id} (FIT-15/FIT-111 contract)"
        )


# ── Host name defaults wired in app.js ───────────────────────────

def test_friendly_host_defaults_wired_in_js():
    """The adapter returns the generic role names "primary"/"fallback".
    The UI maps those to the deployment's real hosts: ASUS GX10 + Mac
    Studio. Lock in both strings so the headline doesn't silently drop
    back to "primary"/"fallback" after a refactor."""
    assert "AI_PRIMARY_HOST_DEFAULT = 'ASUS GX10'" in APP_JS
    assert "AI_FALLBACK_HOST_DEFAULT = 'Mac Studio'" in APP_JS
    # Initial markup also displays these defaults so the card is not
    # blank before the first /api/ai/health response lands.
    assert ">ASUS GX10<" in INDEX_HTML
    assert ">Mac Studio<" in INDEX_HTML


# ── Three required headline states ───────────────────────────────

def test_headline_renders_three_states_with_correct_chip_class():
    """Acceptance criteria:
      1. Primary healthy           → "Ready"          + ok chip
      2. Primary down, fallback up → "Fallback active" + warn chip
      3. Both down / unreachable   → "AI offline"     + stale chip
    """
    headline_fn = _slice_function(APP_JS, "function _aiCoachHeadlineFromHealth")

    # 1) Primary healthy
    assert "'Ready'" in headline_fn
    assert "state-chip ok" in headline_fn

    # 2) Fallback active uses warn (NOT stale) — explicit acceptance
    #    criterion: "Warn-color used for fallback-active state".
    assert "'Fallback active'" in headline_fn
    assert "state-chip warn" in headline_fn

    # 3) Both hosts unreachable uses stale.
    assert "'AI offline'" in headline_fn
    assert "state-chip stale" in headline_fn


def test_fallback_active_requires_loaded_fallback_model():
    """`active_role: fallback` alone is adapter metadata, not proof
    that the Mac Studio fallback is serving traffic. The headline can
    claim "Fallback active" only when the fallback check itself is
    reachable and has the target model loaded."""
    headline_fn = _slice_function(APP_JS, "function _aiCoachHeadlineFromHealth")
    assert "const fallbackOk = !!(fallback && fallback.reachable && fallback.model_loaded);" in headline_fn
    assert "if (fallbackOk) {" in headline_fn
    assert "fallbackOk || activeRole === 'fallback'" not in headline_fn


def test_setaicoachunavailable_writes_headline_stale():
    """When neither /api/ai/health nor /api/ai/metrics responds, the
    headline must degrade to the stale "AI offline" state — not just
    the per-row "Unavailable" chips — so the operator can see the
    failure at a glance."""
    fn = _slice_function(APP_JS, "function _setAiCoachUnavailable")
    assert "_renderAiCoachHeadline(" in fn
    assert "'AI offline'" in fn
    assert "state-chip stale" in fn


def test_role_pill_letter_spacing_stays_zero():
    """Branch-local FIT-166 role pills must follow the app typography
    rule that letter spacing is zero."""
    role_pill = STYLE_CSS.split(".ai-role-pill {", 1)[1].split("}", 1)[0]
    assert "letter-spacing: 0;" in role_pill


# ── Helpers ──────────────────────────────────────────────────────

def _slice_function(source: str, marker: str) -> str:
    """Return the body of a top-level function so chip-class assertions
    don't accidentally match elsewhere in app.js."""
    assert marker in source, f"{marker!r} not found in app.js"
    after = source.split(marker, 1)[1]
    # Functions in app.js are formatted with a `    }\n` closing brace at
    # indent level 1 (the IIFE indents everything 4 spaces).
    return after.split("\n    }\n", 1)[0]
