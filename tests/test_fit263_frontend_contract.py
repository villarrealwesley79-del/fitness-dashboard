"""FIT-263 frontend regression contracts."""

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]


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
  'renderGreeting', 'wireEvents', 'switchTabFromHash',
  'renderSyncBanner', 'registerServiceWorker',
  'settleActiveWorkoutDraftAfterAuthScope', 'scheduleAuthScopeRetry',
  'flushSyncQueue', 'saveActiveWorkoutDraftBeforePageHidden',
].forEach((name) => sandbox.__fitSet[name](() => {}));
sandbox.__fitSet.refreshAuthScope(async () => ({ status: 'ready' }));
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
            "renderGreeting", "wireEvents", "switchTabFromHash",
            "renderSyncBanner", "registerServiceWorker",
            "settleActiveWorkoutDraftAfterAuthScope", "scheduleAuthScopeRetry",
            "flushSyncQueue", "saveActiveWorkoutDraftBeforePageHidden",
            "refreshAuthScope", "api",
        ],
    )
    assert output == {
        "afterBoot": 1,
        "whileHidden": 1,
        "afterVisible": 2,
        "dot": "ai-dot ok",
    }


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


def test_page_relationships_describe_invalid_target_fallback():
    content = (ROOT / "docs" / "prd" / "appendix" / "page-relationships.md").read_text(
        encoding="utf-8"
    )

    assert "invalid/non-panel targets fall back to `#tab-dashboard`." in content
    assert "dead tab target" not in content
