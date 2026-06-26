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


def test_mobile_tab_bar_does_not_force_horizontal_overflow():
    css = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".tab-btn {" in css
    tab_btn_block = css.split(".tab-btn {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 0" in tab_btn_block
    assert "min-width: 0" in tab_btn_block
    assert "padding: 6px 4px" in tab_btn_block


def test_training_goal_picker_exposes_single_selection_state():
    html = INDEX_HTML.read_text()
    source = APP_JS.read_text()
    assert 'id="settings-goals"' in html
    assert 'role="radiogroup"' in html
    assert 'aria-label="Training goal"' in html
    assert "function handleGoalOptionKeydown(e)" in source
    assert "['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', ' ', 'Enter'].includes(key)" in source
    assert "let selectedGoalToRestoreFocus = null;" in source
    assert "btn.addEventListener('click', () => selectGoalOption(btn));" in source
    assert "btn.setAttribute('role', 'radio');" in source
    assert "btn.setAttribute('aria-checked', selected ? 'true' : 'false');" in source
    assert "btn.tabIndex = selected ? 0 : -1;" in source
    assert "btn.addEventListener('keydown', handleGoalOptionKeydown);" in source
    assert 'class="goal-check" aria-hidden="true"' in source


def test_viewport_allows_browser_zoom():
    viewport = [
        attrs for tag, attrs in _html_elements()
        if tag == "meta" and attrs.get("name") == "viewport"
    ]
    assert len(viewport) == 1
    content = viewport[0]["content"].lower()
    assert "user-scalable=no" not in content
    assert "maximum-scale=1.0" not in content


def test_core_log_and_settings_controls_have_programmatic_labels():
    elements = _html_elements()
    ids = {
        attrs["id"] for _tag, attrs in elements
        if attrs.get("id")
    }
    label_targets = {
        attrs["for"] for tag, attrs in elements
        if tag == "label" and attrs.get("for")
    }
    expected_targets = {
        "log-date",
        "log-exercise",
        "log-sets",
        "log-reps",
        "log-weight",
        "log-notes",
        "cardio-date",
        "cardio-type",
        "cardio-duration",
        "cardio-hr",
        "cardio-intensity",
        "cardio-notes",
        "recovery-date",
        "recovery-type",
        "recovery-duration",
        "recovery-temp",
        "recovery-notes",
        "settings-date-of-birth",
        "settings-sex",
        "settings-duration",
        "settings-sessions",
        "settings-equipment",
    }

    assert expected_targets <= ids
    assert expected_targets <= label_targets


def test_modal_escape_and_focus_helpers_are_delegated_and_guard_active_workout():
    source = APP_JS.read_text()
    assert "document.addEventListener('keydown', handleModalEscape);" in source
    assert "document.addEventListener('keydown', handleModalTabKeydown);" in source
    assert "document.addEventListener('focusin', handleModalFocusin);" in source
    assert "document.addEventListener('focusout', handleModalFocusout);" in source
    assert "window.addEventListener('focus', handleModalWindowFocus);" in source
    assert "'iframe'," in source
    assert "function bindModalIframeFocusGuards(modal)" in source
    assert 'data-action="focus-source-close"' in source
    assert "function getTopmostModalForFocus()" in source
    assert "const modal = getTopmostModalForFocus();" in source
    assert "function handleModalTabKeydown(e)" in source
    assert "function handleModalFocusin(e)" in source
    assert "function handleModalFocusout()" in source
    assert "function handleModalWindowFocus()" in source
    assert "if (e.key !== 'Tab') return;" in source
    assert "const modal = getTopmostOpenModal();" in source
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


def test_modal_markup_exposes_dialog_semantics_and_labels():
    elements = _html_elements()
    attrs_by_id = {
        attrs["id"]: attrs
        for _tag, attrs in elements
        if attrs.get("id")
    }
    modals = [
        attrs for _tag, attrs in elements
        if "modal" in _class_tokens(attrs)
    ]

    assert modals
    for modal in modals:
        modal_id = modal["id"]
        assert modal["role"] == "dialog", f"{modal_id} must expose dialog role"
        assert modal["aria-modal"] == "true", f"{modal_id} must be modal to assistive tech"
        label_id = modal.get("aria-labelledby")
        assert label_id, f"{modal_id} must point to its visible title"
        assert label_id in attrs_by_id, f"{modal_id} labels missing element {label_id}"
        label_attrs = attrs_by_id[label_id]
        assert "modal-title" in _class_tokens(label_attrs), (
            f"{modal_id} aria-labelledby must point at its modal title"
        )


def test_app_bundle_and_service_worker_versions_were_bumped_for_rollout():
    html = INDEX_HTML.read_text()
    sw = SW_JS.read_text()
    assert "/static/css/style.css?v=20260626-fit238-qol" in html
    assert "/static/js/app-loader.js?v=20260626-fit240-whoop-intake" in html
    assert "/static/js/app.js?v=20260626-fit240-whoop-intake" in (ROOT / "static" / "js" / "app-loader.js").read_text()
    assert "fitness-dashboard-v20260626-fit240-whoop-intake" in sw
