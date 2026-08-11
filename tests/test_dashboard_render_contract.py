from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()


def test_dashboard_painter_keeps_cold_open_placeholders_and_clears_gauge():
    output = run_app_js(
        ["paintDashboardFromState", "state"],
        """
const element = (classes = []) => {
  const values = new Set(classes);
  return {
    textContent: 'stale', innerHTML: '<svg/>', hidden: false, firstChild: null,
    classList: { toggle(name, enabled) { if (enabled) values.add(name); else values.delete(name); }, remove(name) { values.delete(name); }, has(name) { return values.has(name); } },
    removeChild() {}, _classes: values,
  };
};
['readiness-gauge-svg', 'dash-hrv', 'dash-rhr', 'dash-sleep', 'reco-title', 'reco-intensity'].forEach((id) => { sandbox.elements[id] = element(); });
sandbox.elements['reco-why'] = element(['lower-confidence']);
sandbox.elements['reco-confidence-pct'] = element();
sandbox.elements['insight-title'] = element();
sandbox.elements['insight-body'] = element();
e.state.dashboard = null; e.state.oura = null; e.state.reco = null; e.state.ouraSleep = null;
sandbox.__fitSet.gaugeChart(() => { throw new Error('gauge must not paint without readiness'); });
e.paintDashboardFromState();
process.stdout.write(JSON.stringify({
  gauge: sandbox.elements['readiness-gauge-svg'].innerHTML,
  title: sandbox.elements['reco-title'].textContent,
  intensity: sandbox.elements['reco-intensity'].textContent,
  hrv: sandbox.elements['dash-hrv'].textContent,
  why: sandbox.elements['reco-why'].textContent,
  whyLowerConfidence: sandbox.elements['reco-why'].classList.has('lower-confidence'),
  confidence: sandbox.elements['reco-confidence-pct'].textContent,
  insightTitle: sandbox.elements['insight-title'].textContent,
  insightBody: sandbox.elements['insight-body'].textContent,
}));
""",
        mocks=["gaugeChart"],
    )
    assert output == {
        "gauge": "", "title": "—", "intensity": "—", "hrv": "--",
        "why": "Analyzing your readiness, sleep, and training load…",
        "whyLowerConfidence": False, "confidence": "--%",
        "insightTitle": "Gathering data…", "insightBody": "",
    }


def test_dashboard_render_fans_out_independent_fetches():
    output = run_app_js(
        ["renderDashboard", "state"],
        """
const calls = [];
let resolveSlowOura;
const slowOura = new Promise((resolve) => { resolveSlowOura = resolve; });
sandbox.__fitSet.getDashboard(async () => { calls.push('getDashboard'); return {}; });
sandbox.__fitSet.getOuraStatus(async () => { calls.push('getOuraStatus'); return slowOura; });
sandbox.__fitSet.getReco(async () => { calls.push('getReco'); return null; });
sandbox.__fitSet.getOuraSleep(async () => { calls.push('getOuraSleep'); return null; });
sandbox.__fitSet.getOuraTrends(async () => { calls.push('getOuraTrends'); return null; });
sandbox.__fitSet.getHistory(async () => { calls.push('getHistory'); return null; });
sandbox.__fitSet.paintDashboardFromState(() => calls.push('paint'));
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
const render = e.renderDashboard();
await Promise.resolve();
await Promise.resolve();
const beforeResolve = { calls: calls.slice(), paints: calls.filter((name) => name === 'paint').length };
resolveSlowOura(null);
await render;
process.stdout.write(JSON.stringify({ beforeResolve, calls }));
""",
        mocks=["getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory", "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart"],
    )
    assert all(name in output["beforeResolve"]["calls"] for name in ("getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory"))
    assert output["beforeResolve"]["paints"] >= 2
    assert output["calls"].count("paint") >= output["beforeResolve"]["paints"]


def test_progress_insight_visuals_preserve_semantic_icon_and_tone():
    output = run_app_js(
        ["progressInsightVisual"],
        """
process.stdout.write(JSON.stringify([
  e.progressInsightVisual({ type: 'positive', icon: 'trending_up' }),
  e.progressInsightVisual({ type: 'warning', icon: 'pause' }),
  e.progressInsightVisual({ type: 'negative', icon: 'trending_down' }),
  e.progressInsightVisual({ type: 'info', icon: 'fitness_center' }),
  e.progressInsightVisual({ type: 'negative', icon: 'warning' }),
  e.progressInsightVisual({ type: 'danger' }),
]));
""",
    )
    assert output == [
        {"iconClass": "pos", "iconChar": "↑"}, {"iconClass": "warn", "iconChar": "‖"},
        {"iconClass": "neg", "iconChar": "↓"}, {"iconClass": "info", "iconChar": "i"},
        {"iconClass": "neg", "iconChar": "!"}, {"iconClass": "neg", "iconChar": "▲"},
    ]


def test_dashboard_whoop_source_markup_is_present():
    for token in ('id="reco-fresh-whoop"', 'id="btn-reco-sources"', 'id="modal-reco-sources"'):
        assert token in INDEX_HTML


def test_dashboard_whoop_state_resolver_covers_every_supported_state():
    output = run_app_js(
        ["resolveWhoopUiState"],
        """
process.stdout.write(JSON.stringify([
  e.resolveWhoopUiState({ error: 'sync failed' }),
  e.resolveWhoopUiState({ syncing: true }),
  e.resolveWhoopUiState({ reauth_required: true }),
  e.resolveWhoopUiState({ source_kind: 'csv_only' }),
  e.resolveWhoopUiState({ status: 'missing_config' }),
  e.resolveWhoopUiState({ connected: false }),
  e.resolveWhoopUiState({ source_conflict: true }),
  e.resolveWhoopUiState({ calibrating: true }),
  e.resolveWhoopUiState({ pending_score: true }),
  e.resolveWhoopUiState({ unscorable: true }),
  e.resolveWhoopUiState({ status: 'fresh', connected: true }),
  e.resolveWhoopUiState({ status: 'aging', connected: true }),
  e.resolveWhoopUiState({ status: 'stale', connected: true }),
  e.resolveWhoopUiState({ has_data: false, connected: true }),
  e.resolveWhoopUiState({ connected_at: '2026-07-16T00:00:00Z' }),
  e.resolveWhoopUiState(null),
]));
""",
    )
    assert output == [
        "error", "syncing", "reauth_required", "csv_only", "missing_config",
        "disconnected", "source_conflict", "calibrating", "pending_score", "unscorable",
        "fresh", "aging", "stale", "missing", "connected", "disconnected",
    ]


def test_dashboard_painter_merges_whoop_status_into_recommendation_sources():
    output = run_app_js(
        ["paintDashboardFromState", "state"],
        """
const captured = [];
e.state.dashboard = {
  freshness: {
    whoop: { status: 'stale', score_state: 'pending_score', last_data_point: '2026-07-10' },
    oura: { status: 'fresh' },
  },
};
e.state.reco = { suggested_workout: 'strength' };
e.state.whoopStatus = {
  connected: true,
  status: 'connected',
  last_successful_sync_at: '2026-07-16T12:00:00Z',
};
sandbox.__fitSet.renderFreshnessChips(() => {});
sandbox.__fitSet.renderRecommendationSourceSummary((dash, reco, freshness) => {
  captured.push({
    sameDash: dash === e.state.dashboard,
    sameReco: reco === e.state.reco,
    whoop: freshness.whoop,
  });
});
sandbox.__fitSet.renderMacroCard(() => {});
sandbox.__fitSet.sparkline(() => {});
sandbox.__fitSet.paintRetryChip(() => {});
e.paintDashboardFromState();
process.stdout.write(JSON.stringify(captured));
""",
        mocks=[
            "renderFreshnessChips", "renderRecommendationSourceSummary",
            "renderMacroCard", "sparkline", "paintRetryChip",
        ],
    )
    assert output == [{
        "sameDash": True,
        "sameReco": True,
        "whoop": {
            "status": "stale",
            "score_state": "pending_score",
            "last_data_point": "2026-07-10",
            "connected": True,
            "last_successful_sync_at": "2026-07-16T12:00:00Z",
            "ui_state": "pending_score",
        },
    }]


def test_render_stats_clears_stale_insights_for_empty_and_malformed_responses():
    output = run_app_js(
        ["renderStats"],
        """
function element() { return { textContent: '', innerHTML: '', appendChild() {} }; }
['stats-workouts', 'stats-workouts-delta', 'stats-volume', 'stats-volume-delta', 'stats-avg-vol', 'stats-avg-vol-delta', 'stats-rpe', 'stats-rpe-sub', 'stats-sets', 'stats-sets-delta', 'stats-time', 'stats-time-delta', 'chart-muscle-donut', 'muscle-legend'].forEach((id) => { sandbox.elements[id] = element(); });
sandbox.elements['insights-list'] = element();
let insightCall = 0;
sandbox.__fitSet.getHistory(async () => ({ workouts: [] }));
sandbox.__fitSet.getAppleHealthWorkouts(async () => []);
sandbox.__fitSet.renderMuscleRecovery(async () => {});
sandbox.__fitSet.donutChart(() => {});
sandbox.__fitSet.getInsights(async () => (++insightCall === 1 ? { insights: [] } : { insights: { malformed: true } }));
sandbox.elements['insights-list'].innerHTML = '<div class="in-card">stale</div>';
await e.renderStats();
const empty = sandbox.elements['insights-list'].innerHTML;
sandbox.elements['insights-list'].innerHTML = '<div class="in-card">stale again</div>';
await e.renderStats();
const malformed = sandbox.elements['insights-list'].innerHTML;
process.stdout.write(JSON.stringify({ empty, malformed }));
""",
        mocks=["getHistory", "getAppleHealthWorkouts", "renderMuscleRecovery", "donutChart", "getInsights"],
    )
    assert output == {
        "empty": '<div class="empty">Log a few workouts to start tracking progress.</div>',
        "malformed": '<div class="empty">Log a few workouts to start tracking progress.</div>',
    }
