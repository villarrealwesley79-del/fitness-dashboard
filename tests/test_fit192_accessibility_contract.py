from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "templates" / "index.html"
APP_JS = ROOT / "static" / "js" / "app.js"
SW_JS = ROOT / "static" / "js" / "sw.js"


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {key: value or "" for key, value in attrs}))


def _html_elements() -> list[tuple[str, dict[str, str]]]:
    parser = _ElementCollector()
    parser.feed(INDEX_HTML.read_text())
    return parser.elements


def _class_tokens(attrs: dict[str, str]) -> set[str]:
    return set((attrs.get("class") or "").split())


def test_tab_markup_has_complete_aria_relationships():
    elements = _html_elements()
    tab_bar = [
        attrs for tag, attrs in elements
        if tag == "nav" and "tab-bar" in _class_tokens(attrs)
    ]
    assert len(tab_bar) == 1
    assert tab_bar[0]["role"] == "tablist"
    assert tab_bar[0]["aria-label"]

    buttons = [
        attrs for tag, attrs in elements
        if tag == "button" and "tab-btn" in _class_tokens(attrs)
    ]
    panels = [
        attrs for tag, attrs in elements
        if tag == "section" and "tab-content" in _class_tokens(attrs)
    ]
    assert buttons
    assert len(buttons) == len(panels)

    buttons_by_panel = {button["data-tab"]: button for button in buttons}
    panels_by_id = {panel["id"]: panel for panel in panels}
    assert set(buttons_by_panel) == set(panels_by_id)

    for panel_id, button in buttons_by_panel.items():
        panel = panels_by_id[panel_id]
        assert button["role"] == "tab"
        assert button["aria-controls"] == panel_id
        assert button["id"]
        assert panel["role"] == "tabpanel"
        assert panel["aria-labelledby"] == button["id"]

        active = "active" in _class_tokens(button)
        assert button["aria-selected"] == ("true" if active else "false")
        assert panel["aria-hidden"] == ("false" if active else "true")
        if not active:
            assert button["tabindex"] == "-1"


def test_switch_tab_keeps_aria_selected_and_panel_hidden_state_in_sync():
    source = APP_JS.read_text()
    assert "el.setAttribute('aria-hidden', active ? 'false' : 'true');" in source
    assert "b.setAttribute('aria-selected', active ? 'true' : 'false');" in source
    assert "b.tabIndex = active ? 0 : -1;" in source
    assert "btn.addEventListener('keydown', handleTabKeydown);" in source
    assert "['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)" in source


def test_modal_escape_and_focus_helpers_are_delegated_and_guard_active_workout():
    source = APP_JS.read_text()
    assert "document.addEventListener('keydown', handleModalEscape);" in source
    assert "if (e.key !== 'Escape') return;" in source
    assert "modal.id !== 'modal-active'" in source
    assert ".sort((a, b) => (a.__fit192OpenedAt || 0) - (b.__fit192OpenedAt || 0))" in source
    assert "closeModal(modal);" in source
    assert "freshDismiss.addEventListener('click', () => closeModal(modal));" in source
    assert "freshClose.addEventListener('click', () => closeModal(modal));" in source
    assert "el.addEventListener('click', () => closeModal(modal))" in source
    assert "closeModal($('modal-swap'));" in source
    assert "$('modal-swap').hidden = true;" not in source
    assert "focusOpenModal(modal);" in source
    assert "focusOpenModal" in source
    assert "record.target" in source
    assert "!modal.classList || !modal.classList.contains('modal')" in source
    assert "document.addEventListener('keydown', function onEsc" not in source


def test_app_bundle_and_service_worker_versions_were_bumped_for_rollout():
    html = INDEX_HTML.read_text()
    sw = SW_JS.read_text()
    assert "/static/js/app.js?v=20260605-fit236-active-draft" in html
    assert "fitness-dashboard-v20260605-fit236-active-draft" in sw
