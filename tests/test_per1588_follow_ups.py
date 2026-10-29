"""PER-1588 / PER-1248: kept-code follow-ups. Synthetic data only."""

from __future__ import annotations

import importlib
from datetime import date

import pytest

from js_runtime import run_app_js

CSRF = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "BODY_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "delete_current_workout_plan", lambda *_a, **_k: None)
    return module


# ---- server -----------------------------------------------------------------

def test_quick_log_sets_default_rpe_observed_to_false_and_keep_an_explicit_true(fitness_app):
    sets = [
        {"weight_lbs": 100, "reps": 8, "rpe": 7},
        {"weight_lbs": 100, "reps": 8, "rpe": 8, "rpe_observed": True},
        {"weight_lbs": 100, "reps": 8, "rpe": None, "rpe_observed": True},
    ]
    response = fitness_app.app.test_client().post(
        "/api/add-workout",
        json={"date": "2026-10-28", "session_type": "strength", "exercises": [{"machine": "Chest Press", "sets": sets}]},
        headers=CSRF,
    )

    assert response.status_code == 200
    stored = fitness_app.WORKOUTS[-1]["exercises"][0]["sets"]
    assert [(row["rpe"], row["rpe_observed"]) for row in stored] == [(7, False), (8, True), (None, False)]


@pytest.mark.parametrize("missing_id, replaced", [(0, False), ("", True), (None, True)])
def test_backfill_keeps_a_zero_id_and_replaces_empty_ones(fitness_app, missing_id, replaced):
    rows = [{"id": missing_id, "date": "2026-10-28"}]
    fitness_app._backfill_history_ids(rows, "unused.json", "workout")
    assert (rows[0]["id"] != missing_id) is replaced


def test_settings_get_falls_back_to_the_shared_defaults(fitness_app, monkeypatch):
    monkeypatch.setattr(fitness_app, "USER_SETTINGS", {})
    body = fitness_app.app.test_client().get("/api/settings").get_json()
    for key in ("sessions_per_week_target", "available_time_minutes", "target_weight_lbs",
                "target_body_fat_pct", "fatigue_threshold", "equipment_preference", "training_goal"):
        assert body[key] == fitness_app.DEFAULT_SETTINGS[key], key


def test_body_measurement_with_an_empty_date_is_saved_for_today(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/add-body-measurement", json={"date": "", "weight_lbs": 180}, headers=CSRF,
    )
    assert response.status_code == 200
    assert fitness_app.BODY_DATA[-1]["date"] == date.today().isoformat()


def test_body_measurement_accepts_the_left_right_tape_strings_the_form_sends(fitness_app):
    response = fitness_app.app.test_client().post(
        "/api/add-body-measurement", json={"weight_lbs": 180, "arms": "13.5/14", "legs": "22/22.5"}, headers=CSRF,
    )
    assert response.status_code == 200
    assert (fitness_app.BODY_DATA[-1]["arms"], fitness_app.BODY_DATA[-1]["legs"]) == ("13.5/14", "22/22.5")


def test_login_redirect_no_longer_carries_the_dead_shell_reload_flag():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "fd_shell_reload" not in (root / "app.py").read_text() + (root / "auth.py").read_text()


# ---- front end --------------------------------------------------------------

def test_focus_and_tap_never_confirm_an_rpe_but_editing_does():
    out = run_app_js(
        ["rpeInteractionObserves"],
        """
process.stdout.write(JSON.stringify(['click', 'focus', 'pointerdown', 'input', 'change'].map(
  (type) => e.rpeInteractionObserves({ type, isTrusted: true }))));
""",
    )
    assert out == [False, False, False, True, True]


def test_undo_treats_409_as_restored_and_other_errors_as_failures():
    out = run_app_js(
        ["restoreDeletedWorkout", "state"],
        """
const toasts = [];
sandbox.__fitSet.toast((message, kind) => toasts.push([message, kind || 'ok']));
sandbox.__fitSet.renderHistory(async () => {});
sandbox.__fitSet.api(async () => { throw new Error('409 /api/restore-history: {"error":{"code":"conflict"}}'); });
await e.restoreDeletedWorkout({ id: 'w1', date: '2026-10-28' });
sandbox.__fitSet.api(async () => { throw new Error('500 /api/restore-history: boom'); });
await e.restoreDeletedWorkout({ id: 'w1', date: '2026-10-28' });
process.stdout.write(JSON.stringify(toasts));
""",
        mocks=["toast", "renderHistory", "api"],
    )
    assert out == [["Workout restored", "ok"], ["Restore failed", "err"]]


def test_quick_log_sends_rpe_observed_only_after_an_rpe_button_was_tapped():
    out = run_app_js(
        ["logStrength"],
        """
const field = (value, extra = {}) => ({ value, dataset: {}, ...extra });
const rpe = field('7');
const sentBodies = [];
sandbox.elements['log-date'] = field('2026-10-28');
sandbox.elements['log-exercise'] = field('Chest Press', { selectedOptions: [{ dataset: { muscle: 'chest' } }] });
sandbox.elements['log-sets'] = field('2');
sandbox.elements['log-reps'] = field('8');
sandbox.elements['log-weight'] = field('100');
sandbox.elements['log-notes'] = field('');
sandbox.elements['log-rpe'] = rpe;
sandbox.__fitSet.api(async (path, opts) => { sentBodies.push(JSON.parse(opts.body)); return {}; });
sandbox.__fitSet.toast(() => {});
sandbox.__fitSet.invalidateCaches(() => {});
sandbox.__fitSet.prepareLog(() => {});
await e.logStrength();
rpe.dataset.observed = '1';
await e.logStrength();
process.stdout.write(JSON.stringify(sentBodies.map((b) => ({
  session_type: b.session_type, machine: b.exercises[0].machine, muscle: b.exercises[0].muscle_group,
  sets: b.exercises[0].sets.map((s) => [s.set_number, s.weight_lbs, s.reps, s.rpe, s.rpe_observed]),
}))));
""",
        mocks=["api", "toast", "invalidateCaches", "prepareLog"],
    )
    assert [row["sets"] for row in out] == [
        [[1, 100, 8, 7, False], [2, 100, 8, 7, False]],
        [[1, 100, 8, 7, True], [2, 100, 8, 7, True]],
    ]
    assert {(row["session_type"], row["machine"], row["muscle"]) for row in out} == {("strength", "Chest Press", "chest")}


def test_left_right_tape_fields_post_as_one_slash_string_and_require_both_sides():
    out = run_app_js(
        ["bodySidedTapePayload"],
        """
const field = (value) => ({ value });
const set = (id, value) => { sandbox.elements[id] = field(value); };
['arms-l', 'arms-r', 'legs-l', 'legs-r'].forEach((k) => set('body-log-' + k, ''));
const empty = e.bodySidedTapePayload();
set('body-log-arms-l', '13.5'); set('body-log-arms-r', '14');
const arms = e.bodySidedTapePayload();
set('body-log-legs-l', '22');
const half = e.bodySidedTapePayload();
process.stdout.write(JSON.stringify({ empty, arms, half }));
""",
    )
    assert out == {"empty": {}, "arms": {"arms": "13.5/14"}, "half": None}


def test_fmt_date_cannot_emit_markup_into_history_rows():
    out = run_app_js(
        ["fmtDate"],
        """
process.stdout.write(JSON.stringify([
  e.fmtDate('2026-10-28<img src=x onerror=alert(1)>'),
  e.fmtDate('<img src=x onerror=alert(1)>'),
]));
""",
    )
    assert not any("<" in value or ">" in value for value in out), out


# ---- PER-1248: overlapping requests, late retries, independent failures -----

ROUTER = """
sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };  // the vm has none; timeouts never fire here
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const held = {};   // url -> [{resolve}] for requests the scenario answers by hand
const answers = {}; // url -> function(callIndex) => Response | 'hold' | Error
const calls = {};
sandbox.fetch = (url) => {
  const key = String(url).split('?')[0];
  const n = calls[key] = (calls[key] || 0) + 1;
  const answer = answers[key] ? answers[key](n) : json({});
  if (answer instanceof Error) return Promise.reject(answer);
  if (answer !== 'hold') return Promise.resolve(answer);
  return new Promise((resolve) => { (held[key] = held[key] || []).push({ resolve }); });
};
"""


def test_an_older_response_that_resolves_last_does_not_overwrite_newer_data():
    out = run_app_js(
        ["getReco", "state"],
        ROUTER + """
answers['/api/recommendation/smart'] = () => 'hold';
const older = e.getReco(true);
const newer = e.getReco(true);
held['/api/recommendation/smart'][1].resolve(json({ id: 'newer' }));
await newer;
held['/api/recommendation/smart'][0].resolve(json({ id: 'older' }));
await older;
process.stdout.write(JSON.stringify({ cached: e.state.reco.id }));
""",
    )
    assert out == {"cached": "newer"}


def test_invalidate_caches_discards_responses_that_were_already_in_flight():
    out = run_app_js(
        ["getDashboard", "invalidateCaches", "state"],
        ROUTER + """
answers['/api/dashboard'] = () => 'hold';
const pending = e.getDashboard(true);
e.invalidateCaches();
held['/api/dashboard'][0].resolve(json({ next_workout: { id: 'stale-plan' } }));
await pending;
process.stdout.write(JSON.stringify({ dashboard: e.state.dashboard, nextWorkout: e.state.nextWorkout }));
""",
    )
    assert out == {"dashboard": None, "nextWorkout": None}


def test_a_late_retry_cannot_repaint_or_overwrite_a_newer_render():
    out = run_app_js(
        ["renderDashboard", "paintRetryChip", "getDashboard", "invalidateCaches", "state"],
        ROUTER + """
let paints = 0;
const chip = { hidden: false, disabled: false };
sandbox.elements['reco-retry'] = chip;
sandbox.__fitSet.paintDashboardFromState(() => { paints += 1; });
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
answers['/api/dashboard'] = (n) => n === 1 ? 'hold' : json({ next_workout: { id: 'newer-plan' } });
e.paintRetryChip('reco-retry', true, 'recoError', async () => { await e.getDashboard(true); });
const lateRetry = chip.onclick();
await Promise.resolve();
e.invalidateCaches();
await e.renderDashboard();
const afterNewRender = { plan: e.state.dashboard.next_workout.id, paints };
held['/api/dashboard'][0].resolve(json({ next_workout: { id: 'older-plan' } }));
await lateRetry;
process.stdout.write(JSON.stringify({ afterNewRender, plan: e.state.dashboard.next_workout.id, paints, chipDisabled: chip.disabled }));
""",
        mocks=["paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart"],
    )
    assert out["afterNewRender"]["plan"] == "newer-plan"
    assert out["plan"] == "newer-plan"
    assert out["paints"] == out["afterNewRender"]["paints"]
    assert out["chipDisabled"] is True  # the stale retry left the chip alone


def test_a_superseded_render_does_not_paint_its_late_charts():
    out = run_app_js(
        ["renderDashboard", "invalidateCaches", "state"],
        ROUTER + """
const trendPaints = [];
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
sandbox.__fitSet.paintReadinessTrendChart((trends) => trendPaints.push(trends && trends.tag));
answers['/api/wearables/trends'] = (n) => n === 1 ? 'hold' : json({ tag: 'newer' });
const first = e.renderDashboard();
await Promise.resolve();
e.invalidateCaches();
await e.renderDashboard();
held['/api/wearables/trends'][0].resolve(json({ tag: 'older' }));
await first;
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(trendPaints));
""",
        mocks=["paintDashboardFromState", "paintVolumeChart", "paintReadinessTrendChart"],
    )
    assert out == ["newer"]


def test_dashboard_smart_sleep_and_trends_fail_independently():
    out = run_app_js(
        ["renderDashboard", "state"],
        ROUTER + """
sandbox.__fitSet.paintDashboardFromState(() => {});
sandbox.__fitSet.paintReadinessTrendChart(() => {});
sandbox.__fitSet.paintVolumeChart(() => {});
answers['/api/dashboard'] = () => json({ error: 'boom' }, 500);
answers['/api/recommendation/smart'] = () => json({ id: 'smart' });
answers['/api/sleep/summary'] = () => json({ id: 'sleep' });
answers['/api/wearables/trends'] = () => new Error('network down');
answers['/api/wearables/today'] = () => json({ id: 'today' });
await e.renderDashboard();
process.stdout.write(JSON.stringify({
  dashboard: e.state.dashboard, smart: e.state.reco && e.state.reco.id, sleep: e.state.ouraSleep && e.state.ouraSleep.id,
  today: e.state.oura && e.state.oura.id, trends: e.state.ouraTrends, recoError: e.state.recoError, ouraError: e.state.ouraError,
}));
""",
        mocks=["paintDashboardFromState", "paintReadinessTrendChart", "paintVolumeChart"],
    )
    assert out == {"dashboard": None, "smart": "smart", "sleep": "sleep", "today": "today",
                   "trends": None, "recoError": True, "ouraError": False}


def test_a_failed_offline_refresh_clears_the_dashboard_plan_but_not_an_independent_one():
    out = run_app_js(
        ["getDashboard", "state"],
        ROUTER + """
const fromDashboard = { id: 'dashboard-plan' };
e.state.dashboard = { next_workout: fromDashboard };
e.state.nextWorkout = fromDashboard;
answers['/api/dashboard'] = () => new TypeError('Failed to fetch');
let failed = false;
try { await e.getDashboard(true); } catch { failed = true; }
const cleared = { failed, dashboard: e.state.dashboard, nextWorkout: e.state.nextWorkout };
const own = { id: 'next-workout-endpoint-plan' };
e.state.dashboard = { next_workout: fromDashboard };
e.state.nextWorkout = own;
try { await e.getDashboard(true); } catch { /* offline again */ }
process.stdout.write(JSON.stringify({ cleared, kept: e.state.nextWorkout, dashboard: e.state.dashboard }));
""",
    )
    assert out["cleared"] == {"failed": True, "dashboard": None, "nextWorkout": None}
    assert out["kept"] == {"id": "next-workout-endpoint-plan"} and out["dashboard"] is None
