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
e.switchTab('btn-adjust-plan');
process.stdout.write(JSON.stringify({ currentTab: e.state.currentTab }));
""",
        mocks=["loadTab"],
    )
    assert output == {"currentTab": "tab-dashboard"}


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
