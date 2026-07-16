"""FIT-177: AI Coach headline announces routing state changes to AT."""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()


def test_live_region_element_present_with_required_attrs():
    snippet = INDEX_HTML.split('id="ai-coach-headline-announcement"', 1)[1].split(">", 1)[0]
    assert 'class="visually-hidden"' in snippet
    assert 'aria-live="polite"' in snippet
    assert 'aria-atomic="true"' in snippet


def test_live_region_is_sibling_of_visible_headline_row():
    headline_idx = INDEX_HTML.find('id="ai-coach-headline-row"')
    announcement_idx = INDEX_HTML.find('id="ai-coach-headline-announcement"')
    assert headline_idx != -1 and announcement_idx > headline_idx


def test_fit166_and_legacy_ai_coach_ids_preserved():
    for required_id in (
        'id="ai-coach-headline-row"', 'id="ai-coach-headline-state"',
        'id="ai-coach-headline-host"', 'id="ai-coach-headline-chip"',
        'id="ai-coach-headline-dot"', 'id="ai-coach-headline-detail"',
        'id="ai-primary-host"', 'id="ai-fallback-host"',
        'id="ai-coach-warning-row"', 'id="ai-primary-state"',
        'id="ai-fallback-state"', 'id="ai-metrics-detail"',
    ):
        assert required_id in INDEX_HTML


def test_first_headline_transition_announces_to_live_region():
    output = run_app_js(
        ["_announceAiCoachHeadline"],
        """
sandbox.elements['ai-coach-headline-announcement'] = { textContent: 'unset' };
e._announceAiCoachHeadline({ stateText: 'Ready', hostText: 'ASUS GX10', chipText: 'Online' });
process.stdout.write(JSON.stringify(sandbox.elements['ai-coach-headline-announcement'].textContent));
""",
    )
    assert output == "AI coach: Ready · ASUS GX10"


def test_unchanged_semantic_headline_does_not_reannounce_detail_copy():
    output = run_app_js(
        ["_announceAiCoachHeadline"],
        """
const region = { textContent: '' };
sandbox.elements['ai-coach-headline-announcement'] = region;
e._announceAiCoachHeadline({ stateText: 'Ready', hostText: 'ASUS GX10', chipText: 'Online', detail: 'one' });
region.textContent = 'sentinel';
e._announceAiCoachHeadline({ stateText: 'Ready', hostText: 'ASUS GX10', chipText: 'Online', detail: 'two' });
process.stdout.write(JSON.stringify(region.textContent));
""",
    )
    assert output == "sentinel"


def test_render_health_fields_announces_headline():
    output = run_app_js(
        ["_renderAiHealthFields"],
        """
['ai-coach-headline-state', 'ai-coach-headline-host', 'ai-coach-headline-chip', 'ai-coach-headline-dot', 'ai-coach-headline-detail', 'ai-coach-headline-announcement'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', className: '', hidden: false };
});
e._renderAiHealthFields({ primary: { reachable: true, model_loaded: true }, active_role: 'primary' });
process.stdout.write(JSON.stringify(sandbox.elements['ai-coach-headline-announcement'].textContent));
""",
    )
    assert output == "AI coach: Ready · ASUS GX10"


def test_unavailable_health_announces_offline_headline():
    output = run_app_js(
        ["_setAiCoachUnavailable"],
        """
['ai-coach-headline-state', 'ai-coach-headline-host', 'ai-coach-headline-chip', 'ai-coach-headline-dot', 'ai-coach-headline-detail', 'ai-coach-headline-announcement'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', className: '', hidden: false };
});
e._setAiCoachUnavailable('offline');
process.stdout.write(JSON.stringify(sandbox.elements['ai-coach-headline-announcement'].textContent));
""",
    )
    assert output == "AI coach: AI offline · health endpoint unreachable"


def test_health_null_branch_announces_offline_headline():
    output = run_app_js(
        ["renderAiCoachHealth"],
        """
['ai-coach-headline-state', 'ai-coach-headline-host', 'ai-coach-headline-chip', 'ai-coach-headline-dot', 'ai-coach-headline-detail', 'ai-coach-headline-announcement', 'ai-metrics-fallback-pct', 'ai-metrics-detail'].forEach((id) => {
  sandbox.elements[id] = { textContent: '', className: '', hidden: false };
});
sandbox.__fitSet.api(async (path) => path.endsWith('/metrics') ? { adjust_requests: 0 } : null);
await e.renderAiCoachHealth();
process.stdout.write(JSON.stringify(sandbox.elements['ai-coach-headline-announcement'].textContent));
""",
        mocks=["api"],
    )
    assert output == "AI coach: AI offline · health endpoint unreachable"
