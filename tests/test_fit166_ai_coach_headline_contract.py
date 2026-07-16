"""FIT-166: AI coach card surfaces ASUS-primary / Mac-Studio-fallback.

The Settings panel previously only labelled rows as "Primary" / "Fallback".
FIT-166 adds a card-level headline plus host-name spans so the operator
can tell at a glance whether the ASUS GX10 is serving traffic, the Mac
Studio has taken over, or both hosts are down.

Markup and CSS are stable contracts; headline behavior is exercised through
the Node runtime fixture so this suite does not couple behavior to source
formatting. The goal is just to lock in:

  * the new IDs exist
  * the pre-existing FIT-15 / FIT-111 IDs are still present
  * the three required headline states have their friendly host
    names wired in app.js (`ASUS GX10`, `Mac Studio`)
  * fallback-active uses warn styling and fully-down uses stale
    styling (acceptance criterion).
"""
from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()
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
        'id="ai-fallback-role"',
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

def test_friendly_host_defaults_render_in_initial_markup():
    """The adapter returns the generic role names "primary"/"fallback".
    The UI maps those to the deployment's real hosts: ASUS GX10 + Mac
    Studio. Lock in both strings so the headline doesn't silently drop
    back to "primary"/"fallback" after a refactor."""
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
    output = run_app_js(
        ["_aiCoachHeadlineFromHealth"],
        """
const primary = { reachable: true, model_loaded: true };
const fallback = { reachable: true, model_loaded: true };
process.stdout.write(JSON.stringify([
  e._aiCoachHeadlineFromHealth({ primary, fallback, active_role: 'primary' }),
  e._aiCoachHeadlineFromHealth({ primary: { reachable: false, model_loaded: false }, fallback, active_role: 'fallback' }),
  e._aiCoachHeadlineFromHealth({ primary: { reachable: false, model_loaded: false }, fallback: null }),
]));
""",
    )
    assert [item["stateText"] for item in output] == ["Ready", "Fallback active", "AI offline"]
    assert [item["chipCls"] for item in output] == ["state-chip ok", "state-chip warn", "state-chip stale"]


def test_fallback_active_requires_loaded_fallback_model():
    """`active_role: fallback` alone is adapter metadata, not proof
    that the Mac Studio fallback is serving traffic. The headline can
    claim "Fallback active" only when the fallback check itself is
    reachable and has the target model loaded."""
    output = run_app_js(
        ["_aiCoachHeadlineFromHealth"],
        """
const output = e._aiCoachHeadlineFromHealth({
  primary: { reachable: true, model_loaded: true },
  fallback: { reachable: true, model_loaded: false },
  active_role: 'fallback',
});
process.stdout.write(JSON.stringify(output));
""",
    )
    assert output["stateText"] == "AI offline"


def test_fallback_detail_distinguishes_model_not_loaded_from_unreachable():
    """Fallback-active detail copy must not call a reachable ASUS host
    unreachable just because its target model is not loaded."""
    output = run_app_js(
        ["_aiCoachHeadlineFromHealth"],
        """
const unreachable = e._aiCoachHeadlineFromHealth({
  primary: { reachable: false, model_loaded: false },
  fallback: { name: 'Mac Studio', reachable: true, model_loaded: true },
  active_role: 'fallback',
});
const unloaded = e._aiCoachHeadlineFromHealth({
  primary: { reachable: true, model_loaded: false },
  fallback: { name: 'Mac Studio', reachable: true, model_loaded: true },
  active_role: 'fallback',
});
process.stdout.write(JSON.stringify({ unreachable: unreachable.detail, unloaded: unloaded.detail }));
""",
    )
    assert output["unreachable"].startswith("ASUS GX10 unreachable")
    assert output["unloaded"].startswith("ASUS GX10 model not loaded")


def test_absent_fallback_is_not_labeled_mac_studio():
    """When the backend reports no distinct fallback route, preserve
    that meaning instead of inventing a Mac Studio host."""
    output = run_app_js(
        ["_aiCoachHeadlineFromHealth"],
        """
const output = e._aiCoachHeadlineFromHealth({ primary: { reachable: false, model_loaded: false } });
process.stdout.write(JSON.stringify(output));
""",
    )
    assert output["hostText"] == "ASUS GX10 unavailable"


def test_fit180_no_distinct_fallback_row_copy_is_less_redundant():
    """FIT-180: the no-distinct-fallback row used to read

        host:   No distinct fallback
        chip:   Same as primary
        detail: No distinct fallback endpoint

    which restated "fallback" three times. The host label stays so
    FIT-166's headline math (which interpolates the host name) still
    works, but the chip and detail switch to copy that doesn't repeat
    the word "fallback" in every column."""
    output = run_app_js(
        ["_renderAiHealthFields"],
        """
const ids = ['ai-primary-host', 'ai-fallback-host', 'ai-fallback-role', 'ai-fallback-state', 'ai-fallback-detail'];
ids.forEach((id) => { sandbox.elements[id] = { textContent: '', hidden: false, className: '' }; });
e._renderAiHealthFields({ primary: { reachable: true, model_loaded: true }, fallback: null, active_role: 'primary' });
process.stdout.write(JSON.stringify({
  host: sandbox.elements['ai-fallback-host'].textContent,
  roleHidden: sandbox.elements['ai-fallback-role'].hidden,
  state: sandbox.elements['ai-fallback-state'].textContent,
  detail: sandbox.elements['ai-fallback-detail'].textContent,
}));
""",
        mocks=["_announceAiCoachHeadline"],
    )
    assert output == {
        "host": "No distinct fallback",
        "roleHidden": True,
        "state": "Primary only",
        "detail": "Fallback uses the primary route.",
    }

    # The static "Fallback" role pill is hidden when there is no
    # distinct fallback endpoint so the mobile row does not repeat
    # fallback copy across every column.
    assert 'id="ai-fallback-role">Fallback</span>' in INDEX_HTML


def test_fit180_distinct_fallback_row_still_uses_mac_studio_semantics():
    """FIT-180 must not regress the distinct-fallback path: when a
    fallback check is reported, the row still surfaces the Mac Studio
    host name and uses `_aiCheckLabel(fallback)` for its chip / detail
    instead of the no-fallback copy."""
    output = run_app_js(
        ["_renderAiHealthFields"],
        """
['ai-fallback-host', 'ai-fallback-role', 'ai-fallback-state', 'ai-fallback-detail'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', hidden: false, className: '' };
});
e._renderAiHealthFields({
  primary: { reachable: true, model_loaded: true },
  fallback: { name: 'Mac Studio', reachable: true, model_loaded: true, model: 'llama' },
  active_role: 'fallback',
});
process.stdout.write(JSON.stringify({
  host: sandbox.elements['ai-fallback-host'].textContent,
  hidden: sandbox.elements['ai-fallback-role'].hidden,
  state: sandbox.elements['ai-fallback-state'].textContent,
  detail: sandbox.elements['ai-fallback-detail'].textContent,
}));
""",
        mocks=["_announceAiCoachHeadline"],
    )
    assert output == {"host": "Mac Studio", "hidden": False, "state": "Ready · active", "detail": "llama"}


def test_setaicoachunavailable_writes_headline_stale():
    """When neither /api/ai/health nor /api/ai/metrics responds, the
    headline must degrade to the stale "AI offline" state — not just
    the per-row "Unavailable" chips — so the operator can see the
    failure at a glance."""
    output = run_app_js(
        ["_setAiCoachUnavailable"],
        """
['ai-coach-headline-state', 'ai-coach-headline-host', 'ai-coach-headline-chip', 'ai-coach-headline-detail'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', className: '', hidden: false };
});
e._setAiCoachUnavailable('offline proof');
process.stdout.write(JSON.stringify({
  state: sandbox.elements['ai-coach-headline-state'].textContent,
  host: sandbox.elements['ai-coach-headline-host'].textContent,
  chip: sandbox.elements['ai-coach-headline-chip'].className,
  detail: sandbox.elements['ai-coach-headline-detail'].textContent,
}));
""",
        mocks=["_announceAiCoachHeadline"],
    )
    assert output == {"state": "AI offline", "host": "health endpoint unreachable", "chip": "state-chip stale", "detail": "offline proof"}


def test_role_pill_letter_spacing_stays_zero():
    """Branch-local FIT-166 role pills must follow the app typography
    rule that letter spacing is zero."""
    role_pill = STYLE_CSS.split(".ai-role-pill {", 1)[1].split("}", 1)[0]
    assert "letter-spacing: 0;" in role_pill
