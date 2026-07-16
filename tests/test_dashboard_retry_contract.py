import importlib
from datetime import datetime, timezone
from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]


def test_retry_chips_are_hidden_accessible_buttons():
    template = (ROOT / "templates" / "index.html").read_text()
    for chip_id in ("readiness-retry", "reco-retry", "insight-retry"):
        start = template.index(f'id="{chip_id}"')
        end = template.index(">", start)
        opening_tag = template[template.rfind("<", 0, start): end + 1]
        assert opening_tag.startswith("<button")
        assert " hidden" in opening_tag
        assert 'aria-live="polite"' in opening_tag


def test_retry_chip_css_is_defined():
    css = (ROOT / "static" / "css" / "style.css").read_text()
    block = css.split(".chip-retry {", 1)[1].split("}", 1)[0]
    assert "background:" in block
    assert "color:" in block
    assert "cursor: pointer" in block
    assert ".chip-retry:hover" in css
    assert ".chip-retry:focus-visible" in css


def test_dashboard_fetchers_pass_the_timeout_to_api_at_runtime():
    output = run_app_js(
        ["getDashboard", "getOuraStatus", "getOuraSleep", "getReco", "state"],
        """
const calls = [];
sandbox.__fitSet.api(async (path, opts) => { calls.push({ path, timeoutMs: opts.timeoutMs }); return {}; });
await e.getDashboard(true);
await e.getOuraStatus(true);
await e.getOuraSleep(true);
await e.getReco(true);
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["api"],
    )
    assert [call["timeoutMs"] for call in output] == [30000, 30000, 30000, 30000]
    assert [call["path"] for call in output] == [
        "/api/dashboard",
        "/api/oura/status",
        "/api/oura/sleep-summary",
        "/api/recommendation/smart",
    ]


def test_non_dashboard_fetchers_clear_caches_and_swallow_api_failures():
    output = run_app_js(
        ["getOuraStatus", "getOuraSleep", "getReco", "state"],
        """
Object.assign(e.state, {
  oura: { readiness: 80 }, ouraSleep: { score: 90 }, reco: { reasoning: 'cached' },
  ouraError: false, ouraSleepError: false, recoError: false,
});
sandbox.__fitSet.api(async () => { throw new Error('offline'); });
const values = await Promise.all([e.getOuraStatus(true), e.getOuraSleep(true), e.getReco(true)]);
process.stdout.write(JSON.stringify({
  values,
  caches: { oura: e.state.oura, sleep: e.state.ouraSleep, reco: e.state.reco },
  sentinels: { oura: e.state.ouraError, sleep: e.state.ouraSleepError, reco: e.state.recoError },
}));
""",
        mocks=["api"],
    )
    assert output == {
        "values": [None, None, None],
        "caches": {"oura": None, "sleep": None, "reco": None},
        "sentinels": {"oura": False, "sleep": False, "reco": False},
    }


def test_render_dashboard_resets_and_maps_dashboard_failure_to_reco_retry_state():
    output = run_app_js(
        ["renderDashboard", "state"],
        """
e.state.ouraError = true;
e.state.recoError = true;
e.state.ouraSleepError = true;
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.getDashboard(async () => { throw new Error('dashboard down'); });
sandbox.__fitSet.getOuraStatus(async () => ({ readiness: 82 }));
sandbox.__fitSet.getReco(async () => ({ recommendation: 'strength' }));
sandbox.__fitSet.getOuraSleep(async () => ({ score: 88 }));
sandbox.__fitSet.getOuraTrends(async () => null);
sandbox.__fitSet.getHistory(async () => null);
await e.renderDashboard();
process.stdout.write(JSON.stringify({ oura: e.state.ouraError, reco: e.state.recoError, sleep: e.state.ouraSleepError }));
""",
        mocks=[
            "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart",
            "getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory",
        ],
    )
    assert output == {"oura": False, "reco": True, "sleep": False}


def test_render_dashboard_clears_a_recovered_sentinel_while_other_failures_remain():
    output = run_app_js(
        ["renderDashboard", "state"],
        """
e.state.ouraError = true;
e.state.recoError = true;
e.state.ouraSleepError = true;
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.getDashboard(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({ readiness: 82 }));
sandbox.__fitSet.getReco(async () => null);
sandbox.__fitSet.getOuraSleep(async () => null);
sandbox.__fitSet.getOuraTrends(async () => null);
sandbox.__fitSet.getHistory(async () => null);
await e.renderDashboard();
process.stdout.write(JSON.stringify({ oura: e.state.ouraError, reco: e.state.recoError, sleep: e.state.ouraSleepError }));
""",
        mocks=[
            "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart",
            "getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory",
        ],
    )
    assert output == {"oura": False, "reco": True, "sleep": True}


def test_render_dashboard_drops_a_late_failure_from_an_older_overlapping_render():
    output = run_app_js(
        ["renderDashboard", "state"],
        """
const deferred = () => { let resolve, reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };
const oldOura = deferred();
let renderCount = 0;
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.getDashboard(async () => ({}));
sandbox.__fitSet.getOuraStatus(() => ++renderCount === 1 ? oldOura.promise : Promise.resolve({ readiness: 84 }));
sandbox.__fitSet.getReco(async () => null);
sandbox.__fitSet.getOuraSleep(async () => null);
sandbox.__fitSet.getOuraTrends(async () => null);
sandbox.__fitSet.getHistory(async () => null);
const oldRender = e.renderDashboard();
await Promise.resolve();
const currentRender = e.renderDashboard();
await currentRender;
oldOura.resolve(null);
await oldRender;
process.stdout.write(JSON.stringify({ oura: e.state.ouraError, renderCount }));
""",
        mocks=[
            "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart",
            "getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory",
        ],
    )
    assert output == {"oura": False, "renderCount": 2}


def test_paint_retry_chip_wires_retry_and_updates_sentinel_after_failure():
    output = run_app_js(
        ["paintDashboardFromState", "state"],
        """
const readiness = { hidden: true, disabled: false };
const reco = { hidden: true, disabled: false };
const insight = { hidden: true, disabled: false };
sandbox.elements['readiness-retry'] = readiness;
sandbox.elements['reco-retry'] = reco;
sandbox.elements['insight-retry'] = insight;
e.state.ouraError = true;
e.state.recoError = true;
e.state.ouraSleepError = true;
sandbox.__fitSet.getOuraStatus(async () => null);
e.paintDashboardFromState();
const allWired = [readiness, reco, insight].every((chip) => !chip.hidden && typeof chip.onclick === 'function');
await readiness.onclick();
process.stdout.write(JSON.stringify({ hidden: readiness.hidden, disabled: readiness.disabled, error: e.state.ouraError, allWired }));
""",
        mocks=["getOuraStatus"],
    )
    assert output == {"hidden": False, "disabled": False, "error": True, "allWired": True}


def test_retry_success_clears_sentinel_and_repaints():
    output = run_app_js(
        ["paintRetryChip", "state"],
        """
const chip = { hidden: false, disabled: false };
sandbox.elements['reco-retry'] = chip;
let paints = 0;
sandbox.__fitSet.paintDashboardFromState(() => { paints += 1; });
e.state.recoError = true;
e.paintRetryChip('reco-retry', true, 'recoError', async () => {});
await chip.onclick();
process.stdout.write(JSON.stringify({ hidden: chip.hidden, disabled: chip.disabled, error: e.state.recoError, paints }));
""",
        mocks=["paintDashboardFromState"],
    )
    assert output == {"hidden": True, "disabled": False, "error": False, "paints": 1}


def test_same_render_retry_success_wins_over_original_pending_fetch_failure():
    output = run_app_js(
        ["renderDashboard", "paintRetryChip", "state"],
        """
const deferred = () => { let resolve, reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };
const originalOura = deferred();
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.getDashboard(async () => ({}));
sandbox.__fitSet.getOuraStatus(() => originalOura.promise);
sandbox.__fitSet.getReco(async () => null);
sandbox.__fitSet.getOuraSleep(async () => null);
sandbox.__fitSet.getOuraTrends(async () => null);
sandbox.__fitSet.getHistory(async () => null);
const render = e.renderDashboard();
await Promise.resolve();
const chip = { hidden: false, disabled: false };
sandbox.elements['readiness-retry'] = chip;
e.state.ouraError = true;
e.paintRetryChip('readiness-retry', true, 'ouraError', async () => {});
await chip.onclick();
originalOura.resolve(null);
await render;
process.stdout.write(JSON.stringify({ error: e.state.ouraError, disabled: chip.disabled }));
""",
        mocks=[
            "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart",
            "getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory",
        ],
    )
    assert output == {"error": False, "disabled": False}


def test_stale_retry_completion_cannot_mutate_state_after_newer_dashboard_render():
    output = run_app_js(
        ["renderDashboard", "paintRetryChip", "state"],
        """
const deferred = () => { let resolve, reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };
const oldRetry = deferred();
const chip = { hidden: false, disabled: false };
sandbox.elements['reco-retry'] = chip;
let paints = 0;
sandbox.__fitSet.paintDashboardFromState(() => { paints += 1; });
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.getDashboard(async () => ({}));
sandbox.__fitSet.getOuraStatus(async () => ({}));
sandbox.__fitSet.getReco(async () => ({}));
sandbox.__fitSet.getOuraSleep(async () => ({}));
sandbox.__fitSet.getOuraTrends(async () => null);
sandbox.__fitSet.getHistory(async () => null);
e.state.recoError = true;
e.paintRetryChip('reco-retry', true, 'recoError', () => oldRetry.promise);
const staleClick = chip.onclick();
await Promise.resolve();
await e.renderDashboard();
const afterNewRender = { error: e.state.recoError, paints };
oldRetry.reject(new Error('obsolete retry failed'));
await staleClick;
process.stdout.write(JSON.stringify({
  afterNewRender,
  afterStaleCompletion: { error: e.state.recoError, paints },
}));
""",
        mocks=[
            "paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart",
            "getDashboard", "getOuraStatus", "getReco", "getOuraSleep", "getOuraTrends", "getHistory",
        ],
    )
    assert output["afterStaleCompletion"] == output["afterNewRender"]
    assert output["afterStaleCompletion"] == {
        "error": False,
        "paints": 5,
    }


def test_next_workout_start_uses_cached_plan_when_fresh_load_fails():
    output = run_app_js(
        ["startWorkout", "state"],
        """
const plan = { focus: 'strength', exercises: [{ exercise: 'Squat' }] };
e.state.nextWorkout = plan;
sandbox.__fitSet.getNextWorkout(async () => { throw new Error('offline'); });
sandbox.__fitSet.confirmDiscardActiveWorkoutForStart(() => true);
let started = null;
sandbox.__fitSet.startActiveWorkoutFromRecommendation((value) => { started = value; });
sandbox.__fitSet.renderActiveWorkout(() => {});
sandbox.__fitSet.toast(() => {});
await e.startWorkout();
process.stdout.write(JSON.stringify(started));
""",
        mocks=["getNextWorkout", "confirmDiscardActiveWorkoutForStart", "startActiveWorkoutFromRecommendation", "renderActiveWorkout", "toast"],
    )
    assert output == {"focus": "strength", "exercises": [{"exercise": "Squat"}]}

def test_next_workout_renders_plan_when_optional_copy_fetches_fail():
    output = run_app_js(
        ["renderNextWorkout", "state"],
        """
function node() { return { textContent: '', innerHTML: '', hidden: true, children: [], appendChild(child) { this.children.push(child); } }; }
['nw-title', 'nw-sub', 'nw-duration', 'nw-rpe', 'nw-why', 'nw-exercise-list', 'nw-cardio-card'].forEach((id) => { sandbox.elements[id] = node(); });
e.state.currentTab = 'tab-workout';
sandbox.__fitSet.getNextWorkout(async () => ({ focus: 'strength', goal_name: 'build_strength', estimated_minutes: 45, exercises: [] }));
sandbox.__fitSet.getReco(async () => { throw new Error('reco unavailable'); });
sandbox.__fitSet.getSettings(async () => { throw new Error('settings unavailable'); });
await e.renderNextWorkout();
await Promise.resolve();
process.stdout.write(JSON.stringify({
  title: sandbox.elements['nw-title'].textContent,
  duration: sandbox.elements['nw-duration'].textContent,
  rpe: sandbox.elements['nw-rpe'].textContent,
  why: sandbox.elements['nw-why'].textContent,
  list: sandbox.elements['nw-exercise-list'].innerHTML,
}));
""",
        mocks=["getNextWorkout", "getReco", "getSettings"],
    )
    assert output == {
        "title": "Strength",
        "duration": "45 min",
        "rpe": "RPE —",
        "why": "Your readiness is high and your plan optimizes strength while managing fatigue.",
        "list": '<div class="empty">No exercises planned — rest day.</div>',
    }


def test_next_workout_drops_late_optional_copy_from_an_older_render():
    output = run_app_js(
        ["renderNextWorkout", "state"],
        """
function node() { return { textContent: '', innerHTML: '', hidden: true, children: [], appendChild(child) { this.children.push(child); } }; }
function deferred() { let resolve; const promise = new Promise((ok) => { resolve = ok; }); return { promise, resolve }; }
['nw-title', 'nw-sub', 'nw-duration', 'nw-rpe', 'nw-why', 'nw-exercise-list', 'nw-cardio-card'].forEach((id) => { sandbox.elements[id] = node(); });
e.state.currentTab = 'tab-workout';
const oldReco = deferred();
const oldSettings = deferred();
let planCall = 0;
let recoCall = 0;
let settingsCall = 0;
sandbox.__fitSet.getNextWorkout(async () => (++planCall === 1
  ? { focus: 'strength', exercises: [] }
  : { focus: 'conditioning', exercises: [] }));
sandbox.__fitSet.getReco(() => ++recoCall === 1 ? oldReco.promise : Promise.resolve({ reasoning: 'Current reasoning' }));
sandbox.__fitSet.getSettings(() => ++settingsCall === 1 ? oldSettings.promise : Promise.resolve({ goal_details: { rpe_target: 7 } }));
await e.renderNextWorkout();
await e.renderNextWorkout();
await Promise.resolve();
oldReco.resolve({ reasoning: 'Stale reasoning' });
oldSettings.resolve({ goal_details: { rpe_target: 10 } });
await Promise.resolve();
process.stdout.write(JSON.stringify({
  title: sandbox.elements['nw-title'].textContent,
  why: sandbox.elements['nw-why'].textContent,
  rpe: sandbox.elements['nw-rpe'].textContent,
}));
""",
        mocks=["getNextWorkout", "getReco", "getSettings"],
    )
    assert output == {
        "title": "Conditioning",
        "why": "Current reasoning",
        "rpe": "RPE 7",
    }


def test_invalidate_caches_clears_dashboard_and_workout_state():
    output = run_app_js(
        ["invalidateCaches", "state"],
        """
Object.assign(e.state, { dashboard: {}, oura: {}, ouraSleep: {}, reco: {}, nextWorkout: {}, settings: {} });
e.invalidateCaches();
process.stdout.write(JSON.stringify({ dashboard: e.state.dashboard, reco: e.state.reco, nextWorkout: e.state.nextWorkout, settings: e.state.settings }));
""",
    )
    assert output == {"dashboard": None, "reco": None, "nextWorkout": None, "settings": None}


def test_api_timeout_uses_abort_controller_and_rejects_hung_requests():
    output = run_app_js(
        ["api"],
        """
let aborted = false;
sandbox.AbortController = class {
  constructor() { this.signal = { addEventListener: (_name, callback) => { this._abort = callback; } }; }
  abort() { this._abort(); }
};
sandbox.__fitSet.fetch((path, opts) => new Promise((resolve, reject) => {
  opts.signal.addEventListener('abort', () => { aborted = true; reject(new Error('aborted')); });
}));
let message = '';
try { await e.api('/api/dashboard', { timeoutMs: 1 }); } catch (error) { message = error.message; }
process.stdout.write(JSON.stringify({ aborted, message }));
""",
        mocks=["fetch"],
    )
    assert output["aborted"] is True
    assert output["message"] == "aborted"


def test_handle_unauthorized_preserves_active_workout_and_reload_paths():
    output = run_app_js(
        ["handleUnauthorizedResponse"],
        """
sandbox.location.reload = () => { sandbox.__reloaded = true; };
let toasts = [];
sandbox.__fitSet.toast((message, tone) => toasts.push({ message, tone }));
let active = true;
sandbox.__fitSet.activeWorkoutHasProgress(() => active);
const guarded = new Response(JSON.stringify({ error: 'reload_required' }), { status: 401, headers: { 'content-type': 'application/json' } });
let first = '';
try { await e.handleUnauthorizedResponse(guarded); } catch (error) { first = error.message; }
active = false;
const reload = new Response(JSON.stringify({ error: 'reload_required' }), { status: 401, headers: { 'content-type': 'application/json' } });
let second = '';
try { await e.handleUnauthorizedResponse(reload); } catch (error) { second = error.message; }
process.stdout.write(JSON.stringify({ first, second, reloaded: Boolean(sandbox.__reloaded), toasts }));
""",
        mocks=["toast", "activeWorkoutHasProgress"],
    )
    assert output["first"] == "reload required after workout"
    assert output["second"] == "reload required"
    assert output["reloaded"] is True
    assert output["toasts"] == [{"message": "Update ready after workout. Refresh when finished.", "tone": "warn"}]


def test_fit233_adaptation_release_assets_and_reload_contract():
    template = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    sw = (ROOT / "static" / "js" / "sw.js").read_text()
    app_py = (ROOT / "app.py").read_text()
    auth_py = (ROOT / "auth.py").read_text()

    assert "app-loader.js?v=20260713-fit233-adaptation-polling" in template
    assert "app.js?v=20260713-fit233-adaptation-polling" in loader
    assert "fitness-dashboard-v20260713-fit233-adaptation-polling" in sw
    assert "const STATIC_ASSETS" not in sw
    assert "cache.addAll" not in sw
    assert "caches.keys()" in sw
    assert "keys.map(key => caches.delete(key))" in sw
    assert "client.navigate(client.url)" not in sw
    assert 'APP_SHELL_RELOAD_COOKIE = "fd_shell_reload"' in app_py
    assert 'APP_SHELL_RELOAD_VERSION = "20260525-fit181-controller-reload-r2"' in app_py
    assert "APP_SHELL_RELOAD_COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60" in app_py
    assert '"reload_required"' in app_py
    assert "response.status_code = 401" in app_py
    assert "response.set_cookie(" in app_py
    assert 'request.cookies.get("session")' in app_py
    assert "current_user.is_authenticated" in auth_py
    assert 'request.args.get("next")' in auth_py
    assert 'next_page.startswith("//")' in auth_py
    assert 'fd_shell_reload=20260525-fit181-controller-reload-r2' in auth_py
    assert "@app.route('/gym-now')" in app_py
    assert "Cache-Control\": \"no-store\"" in app_py
    assert "\"Cache-Control\": \"no-store, max-age=0\"" in app_py
    assert "event.request.mode === 'navigate'" in sw
    assert "url.pathname.endsWith('.js')" in sw


def test_fit233_dashboard_shell_defers_heavy_bundle():
    template = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    assert "app-loader.js?v=20260713-fit233-adaptation-polling" in template
    assert '<script src="/static/js/app.js' not in template
    assert "window.addEventListener('load', loadAppBundle, { once: true });" in loader
    assert "script.src = '/static/js/app.js?v=20260713-fit233-adaptation-polling';" in loader
    assert "script.async = true;" in loader


def test_next_workout_endpoint_does_not_fetch_open_wearables(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(
        module,
        "fetch_open_wearables_data",
        lambda: (_ for _ in ()).throw(AssertionError("Open Wearables fetch should not run")),
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "test-user")
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "OPEN_WEARABLES_WORKOUT_MARKER_CACHE", None)

    response = module.app.test_client().get("/api/next-workout")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["next_workout"]["exercises"]
    assert payload["next_workout"]["auth_scope"].startswith("user:")


def test_open_wearables_marker_tolerates_timezone_sleep_events():
    module = importlib.import_module("app")
    payload = {
        "sleep": {
            "events": [{"end_time": datetime.now(timezone.utc).isoformat(), "duration_min": 480, "avg_hr": 60}]
        },
        "workouts": {"events": []},
        "activity_summary": {"summaries": []},
    }

    marker = module._store_open_wearables_recommendation_marker(payload)

    assert marker["sleep"]["duration_min"] == 480
    assert marker["sleep"]["recent"] is True


def test_next_workout_cache_invalidation_is_kept_in_backend_mutation_paths():
    app_py = (ROOT / "app.py").read_text()
    settings_body = app_py.split("def settings():", 1)[1].split("@app.route('/api/settings/equipment'", 1)[0]
    equipment_body = app_py.split("def settings_equipment():", 1)[1].split("@app.route('/api/personal-vocab'", 1)[0]
    assert "global LAST_WORKOUT_RECOMMENDATION" in settings_body
    assert "LAST_WORKOUT_RECOMMENDATION = None" in settings_body
    assert "global LAST_WORKOUT_RECOMMENDATION" in equipment_body
    assert "LAST_WORKOUT_RECOMMENDATION = None" in equipment_body
    for marker in ["def add_workout():", "def add_soreness():", "def add_cardio():", "def add_recovery():", "def sync_oura_sleep():"]:
        body = app_py.split(marker, 1)[1].split("\n\n@app.route", 1)[0]
        assert "global LAST_WORKOUT_RECOMMENDATION" in body
        assert "LAST_WORKOUT_RECOMMENDATION = None" in body


def test_dashboard_release_assets_and_reload_contract_remain_stable():
    app_py = (ROOT / "app.py").read_text()
    auth_py = (ROOT / "auth.py").read_text()
    fingerprint_py = (ROOT / "workout_recommendation_fingerprint.py").read_text()
    template = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    sw = (ROOT / "static" / "js" / "sw.js").read_text()
    assert "@app.route('/api/next-workout')" in app_py
    assert "def _current_workout_training_recommendation():" in app_py
    assert "def _workout_recommendation_fingerprint():" in app_py
    assert "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT != fingerprint" in app_py
    assert "workout_recommendation_fingerprint.build_fingerprint(" in app_py
    assert "day=today_s" in app_py
    assert "get_oura_daily_range(OURA_DB_FILE" in app_py
    assert "_open_wearables_workout_inputs_live()" in app_py
    assert "open_wearables_configured=_open_wearables_workout_inputs_live()" in app_py
    assert "def _open_wearables_recommendation_marker(refresh=False):" in app_py
    assert "open_wearables_marker=_open_wearables_recommendation_marker()" in app_py
    assert "include_open_wearables_readiness=False" in app_py
    assert "apple_health_sync_db_file=_apple_health_sync_db_file()" in app_py
    assert "file_marker(apple_health_sync_db_file)" in fingerprint_py
    assert "healthkit_samples_workout_*.json" in app_py
    assert "training_recommendation=_current_workout_training_recommendation()" in app_py
    assert '"day": day' in fingerprint_py
    assert '"oura": {' in fingerprint_py and '"weather": {' in fingerprint_py
    assert '"open_wearables": {' in fingerprint_py and '"apple_health": {' in fingerprint_py
    assert "app-loader.js?v=20260713-fit233-adaptation-polling" in template
    assert "app.js?v=20260713-fit233-adaptation-polling" in loader
    assert "fitness-dashboard-v20260713-fit233-adaptation-polling" in sw
    assert "APP_SHELL_RELOAD_COOKIE = \"fd_shell_reload\"" in app_py
    assert "APP_SHELL_RELOAD_VERSION = \"20260525-fit181-controller-reload-r2\"" in app_py
    assert '"reload_required"' in app_py
    assert "response.status_code = 401" in app_py
    assert "response.set_cookie(" in app_py
    assert 'request.cookies.get("session")' in app_py
    assert "current_user.is_authenticated" in auth_py
    assert 'request.args.get("next")' in auth_py
    assert 'next_page.startswith("//")' in auth_py
    assert 'fd_shell_reload=20260525-fit181-controller-reload-r2' in auth_py
    assert "@app.route('/gym-now')" in app_py
    assert '"Cache-Control": "no-store"' in app_py
    assert '"Cache-Control": "no-store, max-age=0"' in app_py
    assert "event.request.mode === 'navigate'" in sw
    assert "url.pathname.endsWith('.js')" in sw
    assert "caches.keys()" in sw
    assert "keys.map(key => caches.delete(key))" in sw
    assert "client.navigate(client.url)" not in sw


def test_service_worker_update_handoff_preserves_active_workout_progress():
    output = run_app_js(
        ["registerServiceWorker"],
        """
const calls = [];
let controllerChange;
let updateFound;
let workerStateChange;
let hasProgress = true;
const worker = {
  state: 'installing',
  postMessage(message) { calls.push(['worker-message', message.type]); },
  addEventListener(type, handler) { if (type === 'statechange') workerStateChange = handler; },
};
const registration = {
  waiting: { postMessage(message) { calls.push(['waiting-message', message.type]); } },
  installing: worker,
  addEventListener(type, handler) { if (type === 'updatefound') updateFound = handler; },
  update() { calls.push(['update']); return Promise.resolve(); },
};
sandbox.navigator.serviceWorker = {
  controller: {},
  addEventListener(type, handler) { if (type === 'controllerchange') controllerChange = handler; },
  register(path) { calls.push(['register', path]); return Promise.resolve(registration); },
};
sandbox.location.reload = () => calls.push(['reload']);
sandbox.__fitSet.activeWorkoutHasProgress(() => hasProgress);
sandbox.__fitSet.toast((message) => calls.push(['toast', message]));
e.registerServiceWorker();
await new Promise((resolve) => setTimeout(resolve, 0));
controllerChange();
hasProgress = false;
controllerChange();
controllerChange();
updateFound();
worker.state = 'installed';
workerStateChange();
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["activeWorkoutHasProgress", "toast"],
    )
    assert output == [
        ["register", "/sw.js"],
        ["waiting-message", "SKIP_WAITING"],
        ["update"],
        ["toast", "Update ready after workout. Refresh when finished."],
        ["reload"],
        ["worker-message", "SKIP_WAITING"],
    ]


def test_dashboard_shell_defers_heavy_app_bundle_until_after_load():
    template = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    assert "app-loader.js?v=20260713-fit233-adaptation-polling" in template
    assert '<script src="/static/js/app.js' not in template
    assert "window.addEventListener('load', loadAppBundle, { once: true });" in loader
    assert "script.src = '/static/js/app.js?v=20260713-fit233-adaptation-polling';" in loader
    assert "script.async = true;" in loader
