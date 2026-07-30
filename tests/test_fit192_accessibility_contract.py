from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from js_runtime import run_app_js


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
    output = run_app_js(
        ["switchTab", "wireEvents", "state"],
        """
const makePanel = (id) => ({ id, attrs: {}, classList: { toggle: () => {} }, setAttribute(key, value) { this.attrs[key] = value; } });
const makeTab = (tab) => ({ attrs: { 'data-tab': tab }, handlers: {}, classList: { toggle: () => {} }, tabIndex: -1, getAttribute(key) { return this.attrs[key]; }, setAttribute(key, value) { this.attrs[key] = value; }, addEventListener(name, fn) { this.handlers[name] = fn; }, focus() { this.focused = true; } });
const panels = [makePanel('tab-dashboard'), makePanel('tab-settings')];
const tabs = [makeTab('tab-dashboard'), makeTab('tab-settings')];
sandbox.document.querySelectorAll = (selector) => selector === '.tab-content' ? panels : selector === '.tab-btn' ? tabs : [];
sandbox.scrollTo = () => {};
sandbox.addEventListener = () => {};
const loadCalls = [];
sandbox.__fitSet.loadTab(() => loadCalls.push('load'));
e.wireEvents();
e.switchTab('tab-settings');
const switchState = {
  panelHidden: panels.map((panel) => panel.attrs['aria-hidden']),
  buttonSelected: tabs.map((tab) => tab.attrs['aria-selected']),
  tabIndex: tabs.map((tab) => tab.tabIndex),
  currentTab: e.state.currentTab,
};
let prevented = false;
sandbox.__fitSet.switchTab((tab) => { tabs[1].selectedByKey = tab; });
tabs[0].handlers.keydown({ key: 'ArrowRight', currentTarget: tabs[0], preventDefault: () => { prevented = true; } });
process.stdout.write(JSON.stringify({ switchState, loadCalls, keydownBound: typeof tabs[0].handlers.keydown === 'function', keydown: { prevented, focused: tabs[1].focused, selected: tabs[1].selectedByKey } }));
""",
        mocks=["loadTab", "switchTab"],
    )
    assert output == {
        "switchState": {
            "panelHidden": ["true", "false"],
            "buttonSelected": ["false", "true"],
            "tabIndex": [-1, 0],
            "currentTab": "tab-settings",
        },
        "loadCalls": ["load"],
        "keydownBound": True,
        "keydown": {"prevented": True, "focused": True, "selected": "tab-settings"},
    }


def test_mobile_tab_bar_does_not_force_horizontal_overflow():
    css = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".tab-btn {" in css
    tab_btn_block = css.split(".tab-btn {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 0" in tab_btn_block
    assert "min-width: 0" in tab_btn_block
    assert "padding: 6px 4px" in tab_btn_block


def test_training_goal_picker_exposes_single_selection_state():
    html = INDEX_HTML.read_text()
    assert 'id="settings-goals"' in html
    assert 'role="radiogroup"' in html
    assert 'aria-label="Training goal"' in html
    output = run_app_js(
        ["renderSettings"],
        """
const all = {};
function node(tag = 'div') {
  return { tag, value: '', textContent: '', innerHTML: '', className: '', hidden: false, disabled: false, selected: false,
    dataset: {}, attrs: {}, handlers: {}, children: [], classList: { add() {}, remove() {}, toggle() {} },
    setAttribute(name, value) { this.attrs[name] = value; }, getAttribute(name) { return this.attrs[name]; },
    addEventListener(name, fn) { this.handlers[name] = fn; }, appendChild(child) { this.children.push(child); },
    querySelectorAll(selector) { return selector === '[role="radio"]' ? this.children.filter((child) => child.attrs.role === 'radio') : []; },
    focus() { this.focused = true; },
  };
}
sandbox.document.getElementById = (id) => all[id] || (all[id] = node());
sandbox.document.createElement = (tag) => node(tag);
sandbox.document.querySelectorAll = () => [];
const host = sandbox.document.getElementById('settings-goals');
const selected = [];
sandbox.__fitSet.getSettings(async () => ({
  training_goal: 'strength', available_goals: [
    { value: 'strength', name: 'Strength', description: 'Lift heavier' },
    { value: 'hypertrophy', name: 'Hypertrophy', description: 'Build muscle' },
  ], sex_options: [], time_options: [], equipment_options: [],
}));
sandbox.__fitSet.getOuraStatus(async () => ({}));
sandbox.__fitSet.getWhoopStatus(async () => ({}));
sandbox.__fitSet.getOpenWearablesStatus(async () => ({}));
sandbox.__fitSet.getWearableSources(async () => ([]));
sandbox.__fitSet.api(async (path) => path === '/api/freshness' ? { freshness: {} } : {});
sandbox.__fitSet.updateSetting((value) => selected.push(value.training_goal));
['renderOpenWearablesDetail', 'renderFreshnessChips', 'renderWhoopFreshnessDetail', 'renderOuraFreshnessDetail', 'renderSettingsGroupSummaries', 'renderAppleHealthFreshnessDetail', 'renderAiCoachHealth', 'startAiCoachHealthRefresh', 'renderPushSection'].forEach((name) => sandbox.__fitSet[name](() => {}));
await e.renderSettings();
const options = host.children;
let prevented = false;
options[0].handlers.keydown({ key: 'ArrowRight', currentTarget: options[0], preventDefault: () => { prevented = true; } });
options[1].handlers.click();
process.stdout.write(JSON.stringify({
  roles: options.map((option) => option.attrs.role),
  checked: options.map((option) => option.attrs['aria-checked']),
  tabIndex: options.map((option) => option.tabIndex),
  bindings: options.map((option) => [typeof option.handlers.click, typeof option.handlers.keydown]),
  prevented, focused: options[1].focused, selected,
}));
""",
        mocks=[
            "getSettings", "getOuraStatus", "getWhoopStatus", "getOpenWearablesStatus", "getWearableSources", "api", "updateSetting",
            "renderOpenWearablesDetail", "renderFreshnessChips", "renderWhoopFreshnessDetail", "renderOuraFreshnessDetail",
            "renderSettingsGroupSummaries", "renderAppleHealthFreshnessDetail", "renderAiCoachHealth", "startAiCoachHealthRefresh", "renderPushSection",
        ],
    )
    assert output == {
        "roles": ["radio", "radio"],
        "checked": ["true", "false"],
        "tabIndex": [0, -1],
        "bindings": [["function", "function"], ["function", "function"]],
        "prevented": True,
        "focused": True,
        "selected": ["hypertrophy", "hypertrophy"],
    }


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
    output = run_app_js(
        [
            "watchModalFocus", "handleModalEscape", "handleModalTabKeydown",
            "handleModalFocusin", "handleModalFocusout", "handleModalWindowFocus",
        ],
        """
const documentEvents = [];
const windowEvents = [];
sandbox.document.addEventListener = (name) => documentEvents.push(name);
sandbox.addEventListener = (name) => windowEvents.push(name);
const handlerCalls = [];
const activeModal = {
  id: 'modal-active', hidden: false, isConnected: true,
  classList: { contains: (name) => name === 'modal' },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
};
const ordinaryModal = {
  id: 'modal-settings', hidden: false, isConnected: true,
  classList: { contains: (name) => name === 'modal' },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
  __fit192Close: () => { ordinaryModal.hidden = true; handlerCalls.push('close'); },
};
let openModals = [activeModal];
sandbox.document.querySelectorAll = (selector) => selector === '.modal' ? openModals : [];
e.watchModalFocus();
let activeEscapePrevented = false;
e.handleModalEscape({ key: 'Escape', preventDefault: () => { activeEscapePrevented = true; } });
openModals = [activeModal, ordinaryModal];
let ordinaryEscapePrevented = false;
e.handleModalEscape({ key: 'Escape', preventDefault: () => { ordinaryEscapePrevented = true; } });
const modal = ordinaryModal;
const focusable = [
  { focus: () => handlerCalls.push('first') },
  { focus: () => handlerCalls.push('last') },
];
sandbox.__fitSet.getTopmostModalForFocus(() => modal);
sandbox.__fitSet.getModalFocusableElements(() => focusable);
sandbox.__fitSet.restoreFocusInsideModal(() => handlerCalls.push('restore'));
sandbox.document.activeElement = { id: 'outside' };
let tabPrevented = false;
e.handleModalTabKeydown({ key: 'Tab', shiftKey: false, preventDefault: () => { tabPrevented = true; } });
e.handleModalFocusin({ target: { id: 'outside' } });
e.handleModalFocusout();
sandbox.__fitSet.refreshWhoopAfterOAuthReturn(() => handlerCalls.push('whoop'));
e.handleModalWindowFocus();
await new Promise((resolve) => setTimeout(resolve, 5));
process.stdout.write(JSON.stringify({
  documentEvents, windowEvents, activeEscapePrevented, ordinaryEscapePrevented,
  ordinaryHidden: ordinaryModal.hidden, tabPrevented, handlerCalls,
}));
""",
        mocks=[
            "getTopmostModalForFocus",
            "getModalFocusableElements", "restoreFocusInsideModal",
            "refreshWhoopAfterOAuthReturn",
        ],
    )
    assert output["documentEvents"] == ["keydown", "keydown", "focusin", "focusout"]
    assert output["windowEvents"] == ["focus"]
    assert output["activeEscapePrevented"] is False
    assert output["ordinaryEscapePrevented"] is True
    assert output["ordinaryHidden"] is True
    assert output["tabPrevented"] is True
    assert output["handlerCalls"] == ["close", "first", "restore", "restore", "restore", "whoop"]


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
    assert "/static/js/app-loader.js?v=20260713-fit233-adaptation-polling" in html
    assert "/static/js/app.js?v=20260713-fit233-adaptation-polling" in (ROOT / "static" / "js" / "app-loader.js").read_text()
    assert "fitness-dashboard-v20260713-fit233-adaptation-polling" in sw
