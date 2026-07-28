from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "templates" / "index.html").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()


def test_adaptation_render_gate_keeps_applied_and_stale_but_filters_silent_events():
    output = run_app_js(
        ["workoutAdaptationIsRenderable"],
        """
process.stdout.write(JSON.stringify([
  e.workoutAdaptationIsRenderable({ id: 'a', status: 'applied', change_type: 'changed' }),
  e.workoutAdaptationIsRenderable({ id: 's', status: 'stale', change_type: 'changed' }),
  e.workoutAdaptationIsRenderable({ id: 'n', status: 'applied', change_type: 'none' }),
  e.workoutAdaptationIsRenderable({ id: 'q', status: 'applied', change_type: 'changed', silent: true }),
  e.workoutAdaptationIsRenderable({ id: 'x', status: 'pending', change_type: 'changed' }),
]));
""",
    )
    assert output == [True, True, False, False, False]


def test_adaptation_fetch_uses_backend_feed_and_replaces_status_changed_card():
    output = run_app_js(
        ["fetchWorkoutAdaptationNotices"],
        """
function card(id) { return { dataset: { workoutAdaptationId: id }, removed: false, remove() { this.removed = true; } }; }
const cards = [];
const host = { hidden: true, querySelectorAll: () => cards.filter((item) => !item.removed), appendChild(item) { cards.push(item); } };
sandbox.elements['workout-adaptation-host'] = host;
const shown = [];
sandbox.__fitSet.showWorkoutAdaptationNotice((event) => { shown.push(event.status); host.appendChild(card(event.id)); });
let poll = 0;
sandbox.__fitSet.api(async () => ({ events: [{ id: 'event-1', status: ++poll === 1 ? 'applied' : 'stale', change_type: 'changed' }] }));
await e.fetchWorkoutAdaptationNotices();
await e.fetchWorkoutAdaptationNotices();
process.stdout.write(JSON.stringify({ shown, removed: cards[0].removed, active: host.querySelectorAll().length }));
""",
        mocks=["api", "showWorkoutAdaptationNotice"],
    )
    assert output == {"shown": ["applied", "stale"], "removed": True, "active": 1}


def test_adaptation_and_next_workout_requests_serialize_active_workout_context():
    output = run_app_js(
        ["fetchWorkoutAdaptationNotices", "getNextWorkout", "state"],
        """
e.state.activeWorkout = {
  exercises: [
    { exercise: 'Chest Press', logged_sets: [{ done: true }, { done: true }, { done: false }] },
    { exercise: 'Squat', logged_sets: [{ done: true }] },
  ],
};
const paths = [];
sandbox.__fitSet.api(async (path) => {
  paths.push(path);
  return path.includes('/api/next-workout') ? { next_workout: { id: 'next' } } : { events: [] };
});
await e.fetchWorkoutAdaptationNotices();
e.state.nextWorkout = null;
await e.getNextWorkout(true);
const parsed = paths.map((path) => {
  const url = new URL(path, 'https://fitness.local');
  return {
    pathname: url.pathname,
    activeWorkoutOpen: url.searchParams.get('active_workout_open'),
    completedSets: JSON.parse(url.searchParams.get('completed_sets')),
  };
});
process.stdout.write(JSON.stringify(parsed));
""",
        mocks=["api"],
    )
    assert output == [
        {
            "pathname": "/api/workout-adaptation-events",
            "activeWorkoutOpen": "true",
            "completedSets": {"Chest Press": 2, "Squat": 1},
        },
        {
            "pathname": "/api/next-workout",
            "activeWorkoutOpen": "true",
            "completedSets": {"Chest Press": 2, "Squat": 1},
        },
    ]


def test_adaptation_trigger_callers_refresh_after_dashboard_and_history_rendering():
    output = run_app_js(
        ["refreshMacroCard", "renderBodyInterpretationAndNutritionTrend"],
        """
const calls = [];
sandbox.__fitSet.getDashboard(async () => ({ nutrition_today: { calories: 1200 } }));
sandbox.__fitSet.renderMacroCard(() => {});
sandbox.__fitSet.fetchFoodLogRefreshNotices(async () => {});
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => { calls.push('dashboard'); });
await e.refreshMacroCard();
['body-interpretation-card', 'body-interpretation-notes', 'body-nutrition-card', 'body-nutrition-rows', 'body-nutrition-sub'].forEach((id) => {
  sandbox.elements[id] = { hidden: true, textContent: '', innerHTML: '', querySelectorAll: () => [] };
});
sandbox.__fitSet.api(async () => ({ history: [{ date: '2026-07-16', entries_count: 1, calories: 1200, protein_g: 80 }] }));
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => { calls.push('history'); });
await e.renderBodyInterpretationAndNutritionTrend();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["getDashboard", "renderMacroCard", "fetchFoodLogRefreshNotices", "fetchWorkoutAdaptationNotices", "api"],
    )
    assert output == ["dashboard", "history"]


def test_meal_correction_and_deletion_trigger_adaptation_refresh_after_success():
    output = run_app_js(
        ["saveMealCorrection", "openMealDetailModal"],
        """
function node(value = '') {
  return { value, textContent: '', hidden: false, disabled: false, dataset: {}, className: '', handlers: {},
    setAttribute() {}, addEventListener(name, fn) { this.handlers[name] = fn; },
    cloneNode() { const copy = node(this.value); copy.parentNode = this.parentNode; return copy; },
    parentNode: { replaceChild(next) { sandbox.elements['btn-meal-detail-delete'] = next; } } };
}
['meal-detail-edit-error', 'meal-edit-item', 'meal-edit-portion', 'meal-edit-cal', 'meal-edit-pro', 'meal-edit-carb', 'meal-edit-fat', 'meal-edit-sodium', 'meal-detail-title', 'meal-detail-item', 'meal-detail-portion', 'meal-detail-time', 'meal-detail-source', 'meal-detail-confidence', 'meal-detail-from-image', 'meal-detail-cal', 'meal-detail-pro', 'meal-detail-carb', 'meal-detail-fat', 'meal-detail-sodium', 'meal-detail-stub-notice', 'meal-detail-retention-note', 'meal-detail-view', 'meal-detail-edit', 'meal-detail-foot-view', 'meal-detail-foot-edit', 'modal-meal-detail'].forEach((id) => { sandbox.elements[id] = node(); });
sandbox.elements['meal-edit-item'].value = 'Corrected meal';
sandbox.elements['meal-edit-cal'].value = '500';
sandbox.elements['meal-edit-pro'].value = '30';
sandbox.elements['meal-edit-carb'].value = '45';
sandbox.elements['meal-edit-fat'].value = '12';
sandbox.elements['meal-edit-sodium'].value = '700';
sandbox.elements['btn-meal-detail-delete'] = node();
const calls = [];
sandbox.__fitSet.api(async (path, options) => { calls.push({ path, options }); return {}; });
sandbox.__fitSet.renderBodyInterpretationAndNutritionTrend(async () => {});
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => { calls.push('adaptation'); });
sandbox.__fitSet.toast(() => {});
const modal = sandbox.elements['modal-meal-detail'];
await e.saveMealCorrection({ client_id: 'meal-1', item_name: 'Old meal', date: '2026-07-16', logged_at: '2026-07-16T12:00:00', source: 'manual' }, modal, node());
e.openMealDetailModal({ client_id: 'meal-2', item_name: 'Deleted meal', logged_at: '2026-07-16T13:00:00' });
await sandbox.elements['btn-meal-detail-delete'].handlers.click();
process.stdout.write(JSON.stringify({
  apiPaths: calls.filter((item) => item && item.path).map((item) => item.path),
  correctionBody: JSON.parse(calls.find((item) => item && item.path === '/api/add-nutrition').options.body),
  adaptationCalls: calls.filter((item) => item === 'adaptation').length,
  modalHidden: modal.hidden,
}));
""",
        mocks=["api", "renderBodyInterpretationAndNutritionTrend", "fetchWorkoutAdaptationNotices", "toast"],
    )
    assert output["apiPaths"] == ["/api/add-nutrition", "/api/meal-intake/meal-2"]
    assert output["correctionBody"]["portion_description"] is None
    assert output["adaptationCalls"] == 2
    assert output["modalHidden"] is True


def test_boot_requests_adaptation_notices_after_auth_scope_settles():
    output = run_app_js(
        ["boot"],
        """
sandbox.addEventListener = () => {};
sandbox.setInterval = () => null;
const calls = [];
['renderGreeting', 'wireEvents', 'switchTabFromHash', 'fetchFoodLogRefreshNotices', 'refreshAiStatus', 'renderSyncBanner', 'wireMealComposer', 'registerServiceWorker', 'settleActiveWorkoutDraftAfterAuthScope', 'scheduleMealQueueAuthScopeRetry', 'cleanupOrphanedMealQueuePhotos', 'flushSyncQueue', 'flushMealSyncQueue'].forEach((name) => sandbox.__fitSet[name](() => {}));
sandbox.__fitSet.fetchFoodLogRefreshNotices(async () => {});
sandbox.__fitSet.refreshMealQueueAuthScope(async () => ({ status: 'ready' }));
sandbox.__fitSet.cleanupOrphanedMealQueuePhotos(async () => {});
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => { calls.push('adaptation'); });
sandbox.__fitSet.saveActiveWorkoutDraftBeforePageHidden(() => {});
e.boot();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["renderGreeting", "wireEvents", "switchTabFromHash", "fetchFoodLogRefreshNotices", "refreshAiStatus", "renderSyncBanner", "wireMealComposer", "registerServiceWorker", "refreshMealQueueAuthScope", "settleActiveWorkoutDraftAfterAuthScope", "scheduleMealQueueAuthScopeRetry", "cleanupOrphanedMealQueuePhotos", "flushSyncQueue", "flushMealSyncQueue", "fetchWorkoutAdaptationNotices", "saveActiveWorkoutDraftBeforePageHidden"],
    )
    assert output == ["adaptation"]


def test_adaptation_notice_renders_reason_details_and_accessible_dismiss_control():
    output = run_app_js(
        ["showWorkoutAdaptationNotice"],
        """
function node() { return { className: '', textContent: '', hidden: false, disabled: false, children: [], attrs: {}, handlers: {}, dataset: {}, setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); }, addEventListener(k, fn) { this.handlers[k] = fn; }, remove() { this.removed = true; } }; }
sandbox.document.createElement = node;
sandbox.elements['workout-adaptation-host'] = node();
const apiCalls = [];
sandbox.__fitSet.api(async (path) => { apiCalls.push(path); return {}; });
e.showWorkoutAdaptationNotice({
  id: 'event-1', status: 'applied', change_type: 'changed', reason: 'Nutrition changed',
  after_remaining_plan: { exercises: [] }, nutrition_context: { signals: [{ label: 'protein low' }] },
  reason_metadata: { private_rule: 'audit-secret-metadata' },
  rules: ['audit-secret-rule'], citations: ['audit-secret-citation'], audit_log: ['audit-secret-log'],
});
const card = sandbox.elements['workout-adaptation-host'].children[0];
const visible = [card.children[0].children[0].textContent, card.children[1].textContent, card.children[2].innerHTML].join(' ');
process.stdout.write(JSON.stringify({ className: card.className, role: card.attrs.role, live: card.attrs['aria-live'], reason: card.children[1].textContent, details: card.children[2].className, dismiss: card.children[0].children[1].attrs['aria-label'], visible, apiCalls }));
""",
        mocks=["api"],
    )
    assert {key: output[key] for key in ("className", "role", "live", "reason", "details", "dismiss")} == {
        "className": "card workout-adaptation-card", "role": "status", "live": "polite",
        "reason": "Nutrition changed", "details": "workout-adaptation-details",
        "dismiss": "Dismiss workout update",
    }
    assert "audit-secret" not in output["visible"]
    assert output["apiCalls"] == []


def test_stale_adaptation_notice_hides_the_invalidated_remaining_plan():
    output = run_app_js(
        ["showWorkoutAdaptationNotice"],
        """
function node() { return { className: '', textContent: '', innerHTML: '', hidden: false, children: [], attrs: {}, handlers: {}, dataset: {}, setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); }, addEventListener(k, fn) { this.handlers[k] = fn; }, remove() { this.removed = true; } }; }
sandbox.document.createElement = node;
sandbox.elements['workout-adaptation-host'] = node();
sandbox.__fitSet.api(async () => ({}));
e.showWorkoutAdaptationNotice({
  id: 'event-stale',
  status: 'stale',
  change_type: 'changed',
  reason: 'Source meal was corrected',
  after_remaining_plan: { exercises: [{ name: 'Invalidated Bench Press', target_sets: 5, target_reps: 5 }] },
});
const card = sandbox.elements['workout-adaptation-host'].children[0];
const details = card.children[2];
process.stdout.write(JSON.stringify({
  kicker: card.children[0].children[0].textContent,
  html: details.innerHTML,
}));
""",
        mocks=["api"],
    )
    assert output["kicker"] == "Workout update stale"
    assert "Invalidated Bench Press" not in output["html"]
    assert "Updated remaining plan" not in output["html"]
    assert "workout-adaptation-plan-row" not in output["html"]


def test_adaptation_dismiss_click_acks_removes_card_and_refills_next_event():
    output = run_app_js(
        ["fetchWorkoutAdaptationNotices"],
        """
function node() {
  return { children: [], dataset: {}, attrs: {}, handlers: {}, hidden: false, disabled: false, innerHTML: '',
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    remove() { this.removed = true; } };
}
const cards = [];
const host = { hidden: true, appendChild(card) { cards.push(card); }, get children() { return cards.filter((card) => !card.removed); }, querySelectorAll() { return this.children; } };
sandbox.document.createElement = node;
sandbox.elements['workout-adaptation-host'] = host;
let feedCalls = 0;
const paths = [];
sandbox.__fitSet.api(async (path) => {
  paths.push(path);
  if (path.includes('/ack')) return { ok: true };
  feedCalls += 1;
  return feedCalls === 1
    ? { events: [{ id: 'event-1', status: 'applied', change_type: 'changed', reason: 'Nutrition changed' }] }
    : { events: [{ id: 'event-2', status: 'stale', change_type: 'changed', reason: 'Source meal was corrected' }] };
});
await e.fetchWorkoutAdaptationNotices();
const dismiss = host.children[0].children[0].children[1];
await dismiss.handlers.click();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify({
  ack: paths.find((path) => path.includes('/ack')),
  appended: cards.length,
  active: host.children.length,
  currentId: host.children[0] && host.children[0].dataset.workoutAdaptationId,
  hidden: host.hidden,
}));
""",
        mocks=["api"],
    )
    assert output == {
        "ack": "/api/workout-adaptation-events/event-1/ack",
        "appended": 2,
        "active": 1,
        "currentId": "event-2",
        "hidden": False,
    }


def test_adaptation_dismiss_failure_stays_retryable_through_inflight_poll_and_refill():
    output = run_app_js(
        ["fetchWorkoutAdaptationNotices"],
        """
function node() {
  return { children: [], dataset: {}, attrs: {}, handlers: {}, hidden: false, disabled: false, innerHTML: '',
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    remove() { this.removed = true; } };
}
const cards = [];
const host = { hidden: true, appendChild(card) { cards.push(card); }, get children() { return cards.filter((card) => !card.removed); }, querySelectorAll() { return this.children; } };
sandbox.document.createElement = node;
sandbox.elements['workout-adaptation-host'] = host;
let feedCalls = 0;
let ackAttempts = 0;
let resolvePoll;
const poll = new Promise((resolve) => { resolvePoll = resolve; });
const paths = [];
sandbox.__fitSet.api(async (path) => {
  paths.push(path);
  if (path.includes('/ack')) {
    ackAttempts += 1;
    if (ackAttempts === 1) throw new Error('ack failed');
    return { ok: true };
  }
  feedCalls += 1;
  if (feedCalls === 1) return { events: [{ id: 'event-1', status: 'applied', change_type: 'changed' }] };
  if (feedCalls === 2) return poll;
  return { events: [] };
});
await e.fetchWorkoutAdaptationNotices();
const inFlightPoll = e.fetchWorkoutAdaptationNotices();
await Promise.resolve();
const dismiss = host.children[0].children[0].children[1];
await dismiss.handlers.click();
resolvePoll({ events: [{ id: 'event-1', status: 'applied', change_type: 'changed' }] });
await inFlightPoll;
await new Promise((resolve) => setTimeout(resolve, 0));
const retryable = { active: host.children.length, disabled: dismiss.disabled, appended: cards.length };
await dismiss.handlers.click();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify({
  ackAttempts,
  feedCalls,
  ackPaths: paths.filter((path) => path.includes('/ack')),
  retryable,
  finalActive: host.children.length,
  finalHidden: host.hidden,
}));
""",
        mocks=["api"],
    )
    assert output == {
        "ackAttempts": 2,
        "feedCalls": 4,
        "ackPaths": [
            "/api/workout-adaptation-events/event-1/ack",
            "/api/workout-adaptation-events/event-1/ack",
        ],
        "retryable": {"active": 1, "disabled": False, "appended": 1},
        "finalActive": 0,
        "finalHidden": True,
    }


def test_successful_ack_beats_an_older_inflight_poll_for_the_dismissed_event():
    output = run_app_js(
        ["fetchWorkoutAdaptationNotices"],
        """
function node() {
  return { children: [], dataset: {}, attrs: {}, handlers: {}, hidden: false, disabled: false, innerHTML: '',
    appendChild(child) { this.children.push(child); },
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    remove() { this.removed = true; } };
}
const cards = [];
const host = { hidden: true, appendChild(card) { cards.push(card); }, get children() { return cards.filter((card) => !card.removed); }, querySelectorAll() { return this.children; } };
sandbox.document.createElement = node;
sandbox.elements['workout-adaptation-host'] = host;
let feedCalls = 0;
let resolveOldPoll;
const oldPoll = new Promise((resolve) => { resolveOldPoll = resolve; });
const paths = [];
sandbox.__fitSet.api(async (path) => {
  paths.push(path);
  if (path.includes('/ack')) return { ok: true };
  feedCalls += 1;
  if (feedCalls === 1) return { events: [{ id: 'event-1', status: 'applied', change_type: 'changed' }] };
  if (feedCalls === 2) return oldPoll;
  return { events: [{ id: 'event-2', status: 'stale', change_type: 'changed' }] };
});
await e.fetchWorkoutAdaptationNotices();
const stalePoll = e.fetchWorkoutAdaptationNotices();
await Promise.resolve();
const dismiss = host.children[0].children[0].children[1];
await dismiss.handlers.click();
const afterAck = { active: host.children.length, hidden: host.hidden };
resolveOldPoll({ events: [{ id: 'event-1', status: 'applied', change_type: 'changed' }] });
await stalePoll;
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify({
  afterAck,
  ackPaths: paths.filter((path) => path.includes('/ack')),
  feedCalls,
  appended: cards.length,
  activeIds: host.children.map((card) => card.dataset.workoutAdaptationId),
  hidden: host.hidden,
}));
""",
        mocks=["api"],
    )
    assert output == {
        "afterAck": {"active": 0, "hidden": True},
        "ackPaths": ["/api/workout-adaptation-events/event-1/ack"],
        "feedCalls": 3,
        "appended": 2,
        "activeIds": ["event-2"],
        "hidden": False,
    }


def test_historical_adaptation_does_not_reapply_active_workout():
    output = run_app_js(
        ["applyWorkoutAdaptationToActiveWorkout", "state"],
        """
e.state.activeWorkout = { exercises: [] };
let calls = 0;
sandbox.__fitSet.getNextWorkout(async () => { calls += 1; return null; });
e.applyWorkoutAdaptationToActiveWorkout({ date: '2026-07-10', active_workout: { updated_live: true } });
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["getNextWorkout"],
    )
    assert output == 0


def test_current_adaptation_merges_live_workout_and_rerenders():
    output = run_app_js(
        ["applyWorkoutAdaptationToActiveWorkout", "state"],
        """
e.state.activeWorkout = { exercises: [{ exercise: 'Chest Press', logged_sets: [{ done: true, reps: '8' }] }] };
const calls = [];
const now = new Date();
const currentDay = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
sandbox.__fitSet.getNextWorkout(async () => ({ id: 'next' }));
sandbox.__fitSet.applyAdjustedRecommendationToActiveWorkout((nw, previous) => calls.push({ nw: nw.id, done: previous[0].logged_sets[0].done }));
sandbox.__fitSet.renderActiveWorkout(() => calls.push('render'));
e.applyWorkoutAdaptationToActiveWorkout({ date: currentDay, active_workout: { updated_live: true } });
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["getNextWorkout", "applyAdjustedRecommendationToActiveWorkout", "renderActiveWorkout"],
    )
    assert output == [{"nw": "next", "done": True}, "render"]


def test_current_next_day_adaptation_merges_live_workout_and_rerenders():
    output = run_app_js(
        ["applyWorkoutAdaptationToActiveWorkout", "state"],
        """
e.state.activeWorkout = { exercises: [{ exercise: 'Chest Press', logged_sets: [{ done: true }] }] };
const calls = [];
const now = new Date();
const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
const yesterdayDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
const yesterday = `${yesterdayDate.getFullYear()}-${String(yesterdayDate.getMonth() + 1).padStart(2, '0')}-${String(yesterdayDate.getDate()).padStart(2, '0')}`;
sandbox.__fitSet.getNextWorkout(async () => { calls.push('fetch'); return { id: 'next-day' }; });
sandbox.__fitSet.applyAdjustedRecommendationToActiveWorkout((nw, previous) => calls.push({ nw: nw.id, done: previous[0].logged_sets[0].done }));
sandbox.__fitSet.renderActiveWorkout(() => calls.push('render'));
e.applyWorkoutAdaptationToActiveWorkout({
  date: yesterday,
  created_at: `${today}T00:03:01`,
  applies_to: 'next_day',
  active_workout: { updated_live: true },
});
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["getNextWorkout", "applyAdjustedRecommendationToActiveWorkout", "renderActiveWorkout"],
    )
    assert output == ["fetch", {"nw": "next-day", "done": True}, "render"]


def test_adaptation_host_lives_in_dashboard_tab():
    host_index = INDEX_HTML.index('id="workout-adaptation-host"')
    dash_index = INDEX_HTML.index('id="tab-dashboard"')
    next_tab_index = INDEX_HTML.index('id="tab-workout"') if 'id="tab-workout"' in INDEX_HTML else len(INDEX_HTML)
    assert dash_index < host_index < next_tab_index


def test_adaptation_styles_present_and_calm():
    start = STYLE_CSS.index(".workout-adaptation-host {")
    block = STYLE_CSS[start : STYLE_CSS.index(".analyze-section {", start)]
    assert ".workout-adaptation-card {" in block
    assert ".workout-adaptation-reason {" in block
    assert ".workout-adaptation-details {" in block
    assert ".workout-adaptation-chip {" in block
    assert "overflow-wrap: anywhere" in block
