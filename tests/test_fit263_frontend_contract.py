"""FIT-263 frontend regression contracts."""

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
PHOTO_FOOD_PRD = (ROOT / "docs" / "prd" / "05-photo-food-logging-vision.md").read_text(
    encoding="utf-8"
)


def test_nutrition_hash_targets_existing_log_tab_and_unknown_tabs_fall_back():
    output = run_app_js(
        ["initialTabFromHash", "switchTab", "state"],
        """
const panel = (id) => ({
  id,
  classList: { contains: (name) => name === 'tab-content', toggle() {} },
  setAttribute() {},
});
const panels = [panel('tab-dashboard'), panel('tab-log')];
sandbox.elements['tab-dashboard'] = panels[0];
sandbox.elements['tab-log'] = panels[1];
sandbox.document.querySelectorAll = (selector) => selector === '.tab-content' ? panels : [];
sandbox.scrollTo = () => {};
sandbox.location.hash = '#nutrition';
const nutritionTab = e.initialTabFromHash();
e.switchTab(nutritionTab);
const nutritionSelection = e.state.currentTab;
e.switchTab('btn-ai-status');
process.stdout.write(JSON.stringify({
  nutritionTab,
  nutritionSelection,
  invalidSelection: e.state.currentTab,
}));
""",
        mocks=["loadTab"],
    )
    assert output == {
        "nutritionTab": "tab-log",
        "nutritionSelection": "tab-log",
        "invalidSelection": "tab-dashboard",
    }


def test_non_panel_dom_id_falls_back_to_dashboard_at_runtime():
    output = run_app_js(
        ["switchTab", "state"],
        """
const panel = (id) => ({
  id,
  classList: { contains: (name) => name === 'tab-content', toggle() {} },
  setAttribute() {},
});
const panels = [panel('tab-dashboard'), panel('tab-log')];
sandbox.elements['tab-dashboard'] = panels[0];
sandbox.elements['tab-log'] = panels[1];
sandbox.document.querySelectorAll = (selector) => selector === '.tab-content' ? panels : [];
sandbox.scrollTo = () => {};
e.switchTab('btn-ai-status');
process.stdout.write(JSON.stringify({ currentTab: e.state.currentTab }));
""",
        mocks=["loadTab"],
    )
    assert output == {"currentTab": "tab-dashboard"}


def test_barcode_scan_loop_reuses_cached_dom_elements():
    output = run_app_js(
        ["scanMealBarcodeFrame", "mealComposerState"],
        """
const cachedVideo = { readyState: 4 };
const cachedInput = { value: '' };
let domQueries = 0;
sandbox.document.getElementById = () => { domQueries += 1; return null; };
sandbox.requestAnimationFrame = () => null;
sandbox.__fitSet.mealBarcodeScanCancelled(() => false);
e.mealComposerState.barcodeVideo = cachedVideo;
e.mealComposerState.barcodeInput = cachedInput;
e.mealComposerState.barcodeStream = { getTracks: () => [] };
e.mealComposerState.barcodeDetector = { detect: async () => [] };
e.mealComposerState.barcodeScanLastAt = 0;
await e.scanMealBarcodeFrame(500);
process.stdout.write(JSON.stringify({
  domQueries,
  cachedVideo: e.mealComposerState.barcodeVideo === cachedVideo,
  cachedInput: e.mealComposerState.barcodeInput === cachedInput,
}));
        """,
        mocks=["mealBarcodeScanCancelled"],
    )
    assert output == {"domQueries": 0, "cachedVideo": True, "cachedInput": True}


def test_ai_health_poll_skips_hidden_documents():
    output = run_app_js(
        ["boot"],
        """
sandbox.elements['ai-status-dot'] = { className: '' };
sandbox.setInterval = () => null;
sandbox.addEventListener = () => {};
let visibilityHandler;
sandbox.document.addEventListener = (name, handler) => {
  if (name === 'visibilitychange') visibilityHandler = handler;
};
const apiCalls = [];
sandbox.__fitSet.api(async (path) => {
  apiCalls.push(path);
  return { reachable: true, model_loaded: true };
});
[
  'renderGreeting', 'wireEvents', 'switchTabFromHash', 'fetchFoodLogRefreshNotices',
  'renderSyncBanner', 'wireMealComposer', 'registerServiceWorker',
  'settleActiveWorkoutDraftAfterAuthScope', 'scheduleMealQueueAuthScopeRetry',
  'cleanupOrphanedMealQueuePhotos', 'flushSyncQueue', 'flushMealSyncQueue',
  'fetchWorkoutAdaptationNotices', 'saveActiveWorkoutDraftBeforePageHidden',
].forEach((name) => sandbox.__fitSet[name](() => {}));
sandbox.__fitSet.refreshMealQueueAuthScope(async () => ({ status: 'ready' }));
sandbox.__fitSet.fetchFoodLogRefreshNotices(async () => {});
sandbox.__fitSet.fetchWorkoutAdaptationNotices(async () => {});
sandbox.__fitSet.cleanupOrphanedMealQueuePhotos(async () => {});
e.boot();
await new Promise((resolve) => setTimeout(resolve, 0));
const afterBoot = apiCalls.length;
sandbox.document.visibilityState = 'hidden';
visibilityHandler();
await new Promise((resolve) => setTimeout(resolve, 0));
const whileHidden = apiCalls.length;
sandbox.document.visibilityState = 'visible';
visibilityHandler();
await new Promise((resolve) => setTimeout(resolve, 0));
process.stdout.write(JSON.stringify({
  afterBoot,
  whileHidden,
  afterVisible: apiCalls.length,
  dot: sandbox.elements['ai-status-dot'].className,
}));
""",
        mocks=[
            "renderGreeting", "wireEvents", "switchTabFromHash", "fetchFoodLogRefreshNotices",
            "renderSyncBanner", "wireMealComposer", "registerServiceWorker",
            "settleActiveWorkoutDraftAfterAuthScope", "scheduleMealQueueAuthScopeRetry",
            "cleanupOrphanedMealQueuePhotos", "flushSyncQueue", "flushMealSyncQueue",
            "fetchWorkoutAdaptationNotices", "saveActiveWorkoutDraftBeforePageHidden",
            "refreshMealQueueAuthScope", "api",
        ],
    )
    assert output == {
        "afterBoot": 1,
        "whileHidden": 1,
        "afterVisible": 2,
        "dot": "ai-dot ok",
    }


def test_production_bundle_excludes_fit134_mock_backend():
    output = run_app_js(
        ["submitMealComposer", "mealComposerState"],
        """
sandbox.location.search = '?fit134=mock';
sandbox.__fitSet.mealComposerEls(() => ({ text: { value: 'oatmeal' } }));
sandbox.__fitSet.refreshMealSubmitState(() => {});
let handled;
sandbox.__fitSet.handleMealIntakeResponse((payload, context) => {
  handled = { status: payload.status, clientId: context.clientId };
});
const requests = [];
sandbox.fetch = async (path, options) => {
  requests.push({ path, method: options.method });
  return new Response(JSON.stringify({ status: 'logged' }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
};
e.mealComposerState.draftClientId = 'fit263-client';
await e.submitMealComposer();
process.stdout.write(JSON.stringify({ requests, handled, draftClientId: e.mealComposerState.draftClientId }));
""",
        mocks=["mealComposerEls", "refreshMealSubmitState", "handleMealIntakeResponse"],
    )
    assert output == {
        "requests": [{"path": "/api/meal-intake", "method": "POST"}],
        "handled": {"status": "logged", "clientId": "fit263-client"},
        "draftClientId": None,
    }
    assert "former frontend mock URL has been retired from the production bundle" in PHOTO_FOOD_PRD
    assert "?fit134=mock" not in PHOTO_FOOD_PRD
    assert "isolated `DATA_DIR`" in PHOTO_FOOD_PRD


def test_loader_docs_describe_dom_ready_app_bundle_boot():
    loader_contract = (
        "starts the async app bundle once the DOM is ready: immediately when "
        "`document.readyState` is no longer `loading`, otherwise on one-shot "
        "`DOMContentLoaded`"
    )
    for relative_path in (
        "docs/prd/02-daily-brief-dashboard.md",
        "docs/prd/README.md",
        "docs/performance/FIT-237-page-load.md",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        normalized = " ".join(content.split()).casefold()
        assert " ".join(loader_contract.split()).casefold() in normalized, relative_path
        assert "window load" not in normalized
        assert "window `load`" not in normalized
        assert "browser load event" not in normalized


def test_page_relationships_describe_nutrition_and_invalid_target_fallback():
    content = (ROOT / "docs" / "prd" / "appendix" / "page-relationships.md").read_text(
        encoding="utf-8"
    )

    assert (
        "`#nutrition` maps to the existing `#tab-log` panel, and "
        "invalid/non-panel targets fall back to `#tab-dashboard`."
    ) in content
    assert "dead tab target" not in content
