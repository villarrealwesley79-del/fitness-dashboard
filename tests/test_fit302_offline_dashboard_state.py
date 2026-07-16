from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()


def _block(start: str, end: str) -> str:
    start_index = APP_JS.index(start)
    end_index = APP_JS.index(end, start_index)
    return APP_JS[start_index:end_index]


def _run_node(source: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-302 runtime regression requires node")
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_offline_dashboard_failures_surface_retry_states_and_clear_cached_guidance():
    """All dashboard-owned API failures must degrade independently.

    This executes the real renderDashboard orchestration with deterministic
    rejected/null API results. The paint spy represents the card painters and
    records the same state slices they consume, proving a prior session's
    dashboard/recommendation/Oura data has been invalidated before the offline
    repaint and that each retry sentinel is raised.
    """
    loader_source = _block("async function getDashboard(force = false,", "async function getVitals(")
    oura_source = _block("async function getOuraStatus(force = false", "async function getWhoopStatus(")
    trends_source = _block("async function getOuraTrends(force = false,", "async function getReco(")
    reco_source = _block("async function getReco(force = false,", "async function getInsights(")
    render_source = _block("async function renderDashboard()", "function paintDashboardFromState()")
    script = f"""
const loaderSource = {json.dumps(loader_source)};
const ouraSource = {json.dumps(oura_source)};
const trendsSource = {json.dumps(trends_source)};
const recoSource = {json.dumps(reco_source)};
const renderSource = {json.dumps(render_source)};
const state = {{
  dashboard: {{ next_workout: {{ name: 'stale workout' }} }},
  oura: {{ readiness_score: 99 }},
  reco: {{ reasoning: 'stale prior-session guidance' }},
  ouraSleep: {{ score: 99 }},
  nextWorkout: {{ name: 'stale derived workout' }},
  history: null,
}};
let dashboardRenderGen = 0;
const dashboardSentinelGen = {{ ouraError: 0, recoError: 0, ouraSleepError: 0 }};
const paints = [];
const trendPaints = [];
const apiCalls = [];
const paintDashboardFromState = () => paints.push({{
  dashboard: state.dashboard,
  oura: state.oura,
  reco: state.reco,
  sleep: state.ouraSleep,
  nextWorkout: state.nextWorkout,
  retries: [state.ouraError, state.recoError, state.ouraSleepError],
}});
const DASHBOARD_FETCH_TIMEOUT_MS = 30000;
const api = async path => {{ apiCalls.push(path); throw new Error('offline ' + path); }};
const withActiveWorkoutAdaptationParams = path => path;
const getHistory = async () => null;
const paintReadinessTrendChart = value => trendPaints.push(value);
const paintVolumeChart = () => {{}};
eval(loaderSource + ouraSource + trendsSource + recoSource + renderSource + '\\nglobalThis.renderDashboard = renderDashboard;');
(async () => {{
  await renderDashboard();
  await new Promise(resolve => setImmediate(resolve));
  process.stdout.write(JSON.stringify({{ paints, trendPaints, apiCalls }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    output = _run_node(script)
    final = output["paints"][-1]
    assert final["dashboard"] is None
    assert final["oura"] is None
    assert final["reco"] is None
    assert final["sleep"] is None
    assert final["nextWorkout"] is None
    assert final["retries"] == [True, True, True]
    assert output["trendPaints"] == [None, None]
    assert output["apiCalls"] == [
        "/api/dashboard",
        "/api/oura/status",
        "/api/recommendation/smart",
        "/api/oura/sleep-summary",
        "/api/oura/trends",
    ]


def test_offline_painters_restore_placeholders_instead_of_prior_session_guidance():
    """The real painter source must explicitly replace stale card content."""
    painter = _block("function paintDashboardFromState()", "function paintRetryChip(")

    assert "whyEl.textContent = 'Analyzing your readiness, sleep, and training load…';" in painter
    assert "$('reco-confidence-pct').textContent = '--%';" in painter
    assert "$('insight-title').textContent = 'Gathering data…';" in painter
    assert "$('insight-body').textContent = '';" in painter
    assert "$('glance-steps-goal').textContent = oura && oura.steps != null" in painter
    assert ": 'Pending sync';" in painter
    assert "paintRetryChip('readiness-retry', state.ouraError" in painter
    assert "paintRetryChip('reco-retry', state.recoError" in painter
    assert "paintRetryChip('insight-retry', state.ouraSleepError" in painter


def test_initial_offline_fetches_cannot_commit_after_retry_supersedes_them():
    render = _block("async function renderDashboard()", "function paintDashboardFromState()")

    assert "sentinelGens.ouraError === dashboardSentinelGen.ouraError" in render
    assert "sentinelGens.recoError === dashboardSentinelGen.recoError" in render
    assert "sentinelGens.ouraSleepError === dashboardSentinelGen.ouraSleepError" in render
    assert "getDashboard(true, isRecoCurrent)" in render
    assert "getOuraStatus(true, false, isOuraCurrent)" in render
    assert "getReco(true, isRecoCurrent)" in render
    assert "getOuraSleep(true, isSleepCurrent)" in render


def test_service_worker_update_does_not_force_reload_with_workout_progress():
    """Execute the real controllerchange callback in both workout states."""
    register_source = _block("function registerServiceWorker()", "if (document.readyState === 'loading')")
    script = f"""
const registerSource = {json.dumps(register_source)};
let controllerChange;
let reloads = 0;
const toasts = [];
let hasProgress = true;
const activeWorkoutHasProgress = () => hasProgress;
const toast = message => toasts.push(message);
const window = {{ location: {{ reload: () => reloads++ }} }};
const registration = {{
  waiting: null,
  installing: null,
  addEventListener() {{}},
  update: async () => {{}},
}};
const navigator = {{
  serviceWorker: {{
    addEventListener(type, callback) {{ if (type === 'controllerchange') controllerChange = callback; }},
    register: async () => registration,
    controller: {{}},
  }},
}};
eval(registerSource);
(async () => {{
  registerServiceWorker();
  await new Promise(resolve => setImmediate(resolve));
  controllerChange();
  const active = {{ reloads, toasts: [...toasts] }};
  hasProgress = false;
  controllerChange();
  process.stdout.write(JSON.stringify({{ active, afterWorkout: {{ reloads, toasts }} }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    output = _run_node(script)
    assert output["active"]["reloads"] == 0
    assert output["active"]["toasts"] == ["Update ready after workout. Refresh when finished."]
    assert output["afterWorkout"]["reloads"] == 1
