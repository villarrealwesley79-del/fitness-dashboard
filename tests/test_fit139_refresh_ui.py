from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = ROOT / "static" / "css" / "style.css"


_DOM_FIXTURE = """
function node() {
  return {
    className: '', textContent: '', hidden: false, disabled: false, children: [], attrs: {}, handlers: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(child) { this.children.push(child); },
    addEventListener(k, fn) { this.handlers[k] = fn; },
    remove() { this.removed = true; },
  };
}
sandbox.document.createElement = node;
sandbox.elements['toast-host'] = node();
"""


def test_refresh_notice_fetch_uses_backend_feed_and_deduplicates_events():
    output = run_app_js(
        ["fetchFoodLogRefreshNotices"],
        _DOM_FIXTURE
        + """
const calls = [];
sandbox.__fitSet.api(async (path) => { calls.push(path); return { events: [{ id: 'refresh-1', item_name: 'Rice', source: 'USDA' }] }; });
await e.fetchFoodLogRefreshNotices();
await e.fetchFoodLogRefreshNotices();
process.stdout.write(JSON.stringify({ calls, cards: sandbox.elements['toast-host'].children.length }));
""",
        mocks=["api"],
    )
    assert output["calls"] == ["/api/food-log-refresh-events?unacknowledged=true&limit=10"] * 2
    assert output["cards"] == 1


def test_refresh_notice_renders_accessible_passive_toast_and_ack_removes_it():
    output = run_app_js(
        ["showFoodLogRefreshNotice"],
        _DOM_FIXTURE
        + """
let ackPath = null;
sandbox.__fitSet.api(async (path) => { ackPath = path; return {}; });
e.showFoodLogRefreshNotice({ id: 'refresh-2', item_name: 'Chicken bowl', source: 'verified source' });
const card = sandbox.elements['toast-host'].children[0];
const body = card.children[0];
const dismiss = card.children[1];
const before = { className: card.className, role: card.attrs.role, live: card.attrs['aria-live'], title: body.children[0].textContent, detail: body.children[1].textContent, button: dismiss.className };
await dismiss.handlers.click();
process.stdout.write(JSON.stringify({ before, ackPath, removed: card.removed, disabled: dismiss.disabled }));
""",
        mocks=["api"],
    )
    assert output["before"] == {
        "className": "toast food-log-refresh-toast",
        "role": "status",
        "live": "polite",
        "title": "Updated Chicken bowl nutrition",
        "detail": "Verified source: verified source",
        "button": "food-log-refresh-toast-dismiss",
    }
    assert output["ackPath"] == "/api/food-log-refresh-events/refresh-2/ack"
    assert output["removed"] is True
    assert output["disabled"] is True


def test_refresh_notice_ack_failure_keeps_the_card_retryable():
    output = run_app_js(
        ["showFoodLogRefreshNotice"],
        _DOM_FIXTURE
        + """
let attempts = 0;
sandbox.__fitSet.api(async () => {
  attempts += 1;
  if (attempts === 1) throw new Error('temporary ack failure');
  return {};
});
e.showFoodLogRefreshNotice({ id: 'refresh-retry', item_name: 'Rice', source: 'USDA' });
const card = sandbox.elements['toast-host'].children[0];
const dismiss = card.children[1];
await dismiss.handlers.click();
const afterFailure = { removed: !!card.removed, disabled: dismiss.disabled };
await dismiss.handlers.click();
process.stdout.write(JSON.stringify({ attempts, afterFailure, afterRetry: { removed: card.removed, disabled: dismiss.disabled } }));
""",
        mocks=["api"],
    )
    assert output == {
        "attempts": 2,
        "afterFailure": {"removed": False, "disabled": False},
        "afterRetry": {"removed": True, "disabled": True},
    }


def test_refresh_notice_polling_is_hooked_to_dashboard_history_and_boot():
    output = run_app_js(
        ["refreshMacroCard", "renderBodyInterpretationAndNutritionTrend", "boot"],
        """
const calls = [];
sandbox.__fitSet.getDashboard(async () => ({ nutrition_today: {} }));
sandbox.__fitSet.renderMacroCard(() => {});
sandbox.__fitSet.fetchFoodLogRefreshNotices(async () => { calls.push(calls.length === 0 ? 'dashboard' : calls.length === 1 ? 'history' : 'boot'); });
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => {});
await e.refreshMacroCard();
['body-interpretation-card', 'body-interpretation-notes', 'body-nutrition-card', 'body-nutrition-rows', 'body-nutrition-sub'].forEach((id) => {
  sandbox.elements[id] = { hidden: true, textContent: '', innerHTML: '', querySelectorAll: () => [] };
});
sandbox.__fitSet.api(async () => ({ history: [{ date: '2026-07-16', entries_count: 1, calories: 1200, protein_g: 80 }] }));
await e.renderBodyInterpretationAndNutritionTrend();
sandbox.addEventListener = () => {};
sandbox.setInterval = () => null;
['renderGreeting', 'wireEvents', 'switchTabFromHash', 'refreshAiStatus', 'renderSyncBanner', 'wireMealComposer', 'registerServiceWorker', 'settleActiveWorkoutDraftAfterAuthScope', 'scheduleMealQueueAuthScopeRetry', 'cleanupOrphanedMealQueuePhotos', 'flushSyncQueue', 'flushMealSyncQueue'].forEach((name) => sandbox.__fitSet[name](() => {}));
sandbox.__fitSet.refreshMealQueueAuthScope(async () => ({ status: 'ready' }));
sandbox.__fitSet.cleanupOrphanedMealQueuePhotos(async () => {});
sandbox.__fitSet.saveActiveWorkoutDraftBeforePageHidden(() => {});
e.boot();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify(calls));
""",
        mocks=[
            "getDashboard", "renderMacroCard", "fetchFoodLogRefreshNotices", "fetchWorkoutAdaptationNotices", "api",
            "renderGreeting", "wireEvents", "switchTabFromHash", "refreshAiStatus", "renderSyncBanner", "wireMealComposer", "registerServiceWorker",
            "refreshMealQueueAuthScope", "settleActiveWorkoutDraftAfterAuthScope", "scheduleMealQueueAuthScopeRetry", "cleanupOrphanedMealQueuePhotos",
            "flushSyncQueue", "flushMealSyncQueue", "saveActiveWorkoutDraftBeforePageHidden",
        ],
    )
    assert output == ["dashboard", "history", "boot"]


def test_refresh_notice_styles_are_bounded_for_mobile_and_long_food_names():
    css = STYLE_CSS.read_text()
    start = css.index(".food-log-refresh-toast {")
    notice_css = css[start : css.index(".toast-undo {", start)]
    mobile_start = css.rindex("@media (max-width: 480px)", 0, css.index("/* ======================= ACTIVE WORKOUT SET ROWS"))
    mobile_css = css[mobile_start : mobile_start + 260]
    assert "width: min(92vw, 420px)" in notice_css
    assert "max-width: min(92vw, 420px)" in notice_css
    assert "border-radius: 12px" in notice_css
    assert "white-space: normal" in notice_css
    assert "overflow-wrap: anywhere" in notice_css
    assert ".food-log-refresh-toast-dismiss" in notice_css
    assert "width: min(94vw, 420px)" in mobile_css
