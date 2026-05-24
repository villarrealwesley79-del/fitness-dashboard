"""FIT-177: AI Coach headline announces host-routing state changes to AT.

Screen-reader users could see the visible chip flip from "Ready" to
"Fallback active" or "AI offline", but with no live region the
transition is silent. FIT-177 adds a polite live region as a sibling
of the visible headline row and routes all three headline-producing
paths through a single announcement helper.

These are *static contracts* against the markup and the JS source —
no JSDOM/Playwright/runtime announcement tests, matching the repo's
existing FIT-166 style (see ``test_fit166_ai_coach_headline_contract``).
The goal is to lock in:

  * the live-region element and its a11y attributes
  * the module-level semantic key + early-return gating so the
    30-second poll does not re-announce an unchanged state
  * key composition uses stateText/hostText/chipText and excludes
    ``detail`` (so a detail-line copy tweak doesn't re-announce)
  * the announce helper is invoked from every headline-producing path
  * pre-existing FIT-166 / FIT-15 / FIT-111 IDs remain present
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()


# ── Live-region element + a11y attributes ────────────────────────

def test_live_region_element_present_with_required_attrs():
    """The polite live region must exist as a sibling of the visible
    headline row, use the repo's hidden helper class (``visually-hidden``,
    NOT ``sr-only``), and carry the polite + atomic a11y attributes so
    every state transition is announced as a single utterance."""
    assert 'id="ai-coach-headline-announcement"' in INDEX_HTML
    # Locate the element and assert its attribute set.
    snippet = INDEX_HTML.split('id="ai-coach-headline-announcement"', 1)[1].split(">", 1)[0]
    assert 'class="visually-hidden"' in snippet, (
        "FIT-177 must use the repo's existing visually-hidden helper, "
        "not sr-only — the latter has no CSS rule in this app."
    )
    assert 'aria-live="polite"' in snippet
    assert 'aria-atomic="true"' in snippet


def test_live_region_is_sibling_of_visible_headline_row():
    """The announcement element must sit alongside the headline row
    inside the same AI coach card so AT users hear the routing state
    change at the moment the visible chip flips."""
    headline_idx = INDEX_HTML.find('id="ai-coach-headline-row"')
    announcement_idx = INDEX_HTML.find('id="ai-coach-headline-announcement"')
    assert headline_idx != -1 and announcement_idx != -1
    assert announcement_idx > headline_idx, (
        "FIT-177 announcement element should follow the headline row "
        "so it stays inside the AI coach card."
    )


# ── FIT-166 / FIT-15 / FIT-111 ID preservation ───────────────────

def test_fit166_and_legacy_ai_coach_ids_preserved():
    """FIT-177 must not regress FIT-166's visible headline markup or
    the pre-existing FIT-15 / FIT-111 IDs."""
    for required_id in (
        # FIT-166 headline IDs
        'id="ai-coach-headline-row"',
        'id="ai-coach-headline-state"',
        'id="ai-coach-headline-host"',
        'id="ai-coach-headline-chip"',
        'id="ai-coach-headline-dot"',
        'id="ai-coach-headline-detail"',
        'id="ai-primary-host"',
        'id="ai-fallback-host"',
        # FIT-15 / FIT-111 row IDs
        'id="ai-coach-warning-row"',
        'id="ai-primary-state"',
        'id="ai-fallback-state"',
        'id="ai-metrics-detail"',
    ):
        assert required_id in INDEX_HTML, f"FIT-177 must preserve {required_id}"


# ── Module-level gating key ──────────────────────────────────────

def test_module_level_announcement_key_declared():
    """The semantic key lives at module scope (so it persists across
    the 30-second poll) and starts as ``undefined`` — the explicit
    sentinel that the first render hasn't happened yet, vs. ``''``
    which would mean "we already announced empty"."""
    assert "let _lastAiCoachAnnouncementKey;" in APP_JS, (
        "Module-level gating key must be declared with `let` and no "
        "initialiser so `undefined` represents the first-transition "
        "sentinel."
    )


def test_announce_helper_early_returns_when_key_unchanged():
    """If the same headline state is re-rendered (e.g. by the 30-second
    poll), the helper must early-return so AT users don't hear the
    same announcement every 30 seconds."""
    body = _slice_function(APP_JS, "function _announceAiCoachHeadline")
    assert "if (_lastAiCoachAnnouncementKey === key) return;" in body
    assert "_lastAiCoachAnnouncementKey = key;" in body


def test_announcement_key_composition_excludes_detail():
    """Key intentionally uses stateText/hostText/chipText — NOT detail.
    A detail-line copy tweak (e.g. swapping the model name) must not
    trigger a fresh announcement of an unchanged routing state."""
    body = _slice_function(APP_JS, "function _announceAiCoachHeadline")
    assert "parts.stateText" in body
    assert "parts.hostText" in body
    assert "parts.chipText" in body
    # Key composition must not reference detail.
    key_line = next(
        line for line in body.splitlines()
        if "const key" in line or "let key" in line
    )
    assert "parts.detail" not in key_line, (
        "FIT-177 key composition must exclude detail so detail-only "
        "copy changes don't re-announce."
    )


def test_announce_helper_writes_to_live_region():
    """The helper writes to the live region element by ID. Without
    this, the gating is fine but no announcement actually fires."""
    body = _slice_function(APP_JS, "function _announceAiCoachHeadline")
    assert "$('ai-coach-headline-announcement')" in body
    assert "textContent" in body


# ── All three headline paths wired through the helper ────────────

def test_render_ai_health_fields_announces_headline():
    """Normal health path: _renderAiHealthFields builds the headline
    parts via _aiCoachHeadlineFromHealth and must hand the same parts
    to the announcement helper, not just the visual renderer."""
    body = _slice_function(APP_JS, "function _renderAiHealthFields")
    assert "_renderAiCoachHeadline(" in body
    assert "_announceAiCoachHeadline(" in body


def test_set_ai_coach_unavailable_announces_headline():
    """Degraded path: when both health and metrics endpoints are
    unreachable, _setAiCoachUnavailable must announce the offline
    state, not just paint it."""
    body = _slice_function(APP_JS, "function _setAiCoachUnavailable")
    assert "_renderAiCoachHeadline(" in body
    assert "_announceAiCoachHeadline(" in body


def test_render_ai_coach_health_null_branch_announces_headline():
    """Health-null branch: /api/ai/metrics succeeded but
    /api/ai/health returned non-JSON. The headline still degrades to
    AI offline — and AT users must hear it."""
    body = _slice_function(APP_JS, "async function renderAiCoachHealth")
    # There are two _renderAiCoachHeadline call-sites overall (one in
    # _setAiCoachUnavailable, one in the health-null else branch). The
    # one inside renderAiCoachHealth's own body must also announce.
    assert "_renderAiCoachHeadline(" in body
    assert "_announceAiCoachHeadline(" in body


# ── Helpers ──────────────────────────────────────────────────────

def _slice_function(source: str, marker: str) -> str:
    """Return the body of a top-level function so assertions don't
    accidentally match elsewhere in app.js. Mirrors the helper in
    ``test_fit166_ai_coach_headline_contract``."""
    assert marker in source, f"{marker!r} not found in app.js"
    after = source.split(marker, 1)[1]
    return after.split("\n    }\n", 1)[0]
