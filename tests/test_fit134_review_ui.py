"""FIT-134 multi-item meal review runtime contracts."""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def test_normalize_and_accept_body_preserve_backend_fields_and_local_times():
    output = run_app_js(
        ["normalizeMealV2Entry", "buildMealV2AcceptBody", "mealComposerState"],
        """
e.mealComposerState.pending = [];
const payload = {
  meal_id: 'meal-1', meal_type: 'dinner', meal_totals: { calories: 800 },
  local_timestamp: '2026-07-16T18:00:00', local_date: '2026-07-16', local_iso: '2026-07-16T18:00:00-05:00',
  followup: { available: false, used: true }, save_blocked_item_ids: [],
  items: [
    { item_id: 'included', name: 'Rice', status: 'included', estimate: { calories: 400, ambiguous: false, source: 'usda' } },
    { item_id: 'skipped', name: 'Sauce', status: 'skipped', original_estimate: { calories: 100, ambiguous: true, source: 'ai' } },
  ],
};
const entry = e.normalizeMealV2Entry(payload);
const body = e.buildMealV2AcceptBody(entry);
process.stdout.write(JSON.stringify({ entry: { mealType: entry.meal_type, followup: entry.lastFollowupAnswered, local: [entry.local_timestamp, entry.local_date, entry.local_iso] }, body }));
""",
    )
    assert output["entry"] == {"mealType": "dinner", "followup": True, "local": ["2026-07-16T18:00:00", "2026-07-16", "2026-07-16T18:00:00-05:00"]}
    assert output["body"]["meal_id"] == "meal-1"
    assert output["body"]["items"][0]["estimate"]["meal_type"] == "dinner"
    assert output["body"]["items"][1]["estimate"] == {"calories": 100, "ambiguous": True, "source": "ai"}
    assert output["body"]["local_date"] == "2026-07-16"


def test_apply_refresh_replaces_entry_and_expands_new_blocked_items():
    output = run_app_js(
        ["normalizeMealV2Entry", "applyMealV2Refresh", "mealComposerState"],
        """
const existing = e.normalizeMealV2Entry({
  meal_id: 'meal-1', meal_type: 'lunch', items: [],
  policy: { reason: 'keep' }, save_blocked_item_ids: [],
});
existing.expandedItems.add('old');
e.mealComposerState.pending = [existing];
sandbox.__fitSet.renderMealPendingList(() => {});
e.applyMealV2Refresh('meal-1', { meal_id: 'meal-1', meal_type: 'lunch', items: [{ item_id: 'new', status: 'included' }], save_blocked_item_ids: ['new'] });
const entry = e.mealComposerState.pending[0];
process.stdout.write(JSON.stringify({ ids: Array.from(entry.expandedItems), blocked: entry.save_blocked_item_ids, items: entry.items.map((item) => item.item_id), policy: entry.policy }));
""",
        mocks=["renderMealPendingList"],
    )
    assert output == {"ids": ["old", "new"], "blocked": ["new"], "items": ["new"], "policy": {"reason": "keep"}}


def test_item_renderer_enforces_source_backed_candidates_and_pending_disabled_controls():
    output = run_app_js(
        ["buildMealReviewV2ItemHtml"],
        """
const html = e.buildMealReviewV2ItemHtml({
  item_id: 'combo', name: 'Combo', status: 'included', branded_combo_ai_only: true,
  candidates: [{ candidate_id: 'ai', name: 'AI guess', source_backed: false }, { candidate_id: 'real', name: 'Verified', source_backed: true }],
}, { blocked: true, expanded: true, pendingRefresh: true, mealId: 'meal-1' });
process.stdout.write(JSON.stringify(html));
""",
    )
    assert "AI-only restaurant combo" in output
    assert 'data-candidate-id="real"' in output
    assert 'data-candidate-id="ai"' not in output
    candidate_tag = output.split('data-candidate-id="real"', 1)[1].split(">", 1)[0]
    assert " disabled" in candidate_tag
    assert 'data-action="portion-edit-open" disabled' in output


def test_food_review_renderers_do_not_leak_workout_adaptation_copy():
    output = run_app_js(
        ["buildMealReviewCardV2", "buildMealReviewV2ItemHtml"],
        """
sandbox.__fitSet.wireMealReviewCardV2(() => {});
sandbox.document.createElement = () => ({
  className: '', innerHTML: '', attrs: {}, classList: { add() {} },
  setAttribute(name, value) { this.attrs[name] = value; },
});
const item = { item_id: 'item-1', name: 'Rice bowl', status: 'included', calories: 450, source: { kind: 'usda', label: 'USDA' } };
const entry = {
  __v2: true, meal_id: 'meal-1', meal_type: 'dinner', meal_totals: { calories: 450 },
  followup: null, save_blocked_item_ids: [], expandedItems: new Set(), pendingRefresh: false, items: [item],
};
const card = e.buildMealReviewCardV2(entry).innerHTML;
const itemHtml = e.buildMealReviewV2ItemHtml(item, { mealId: 'meal-1' });
process.stdout.write(JSON.stringify(`${card}\n${itemHtml}`));
""",
        mocks=["wireMealReviewCardV2"],
    )
    forbidden = [
        "workout updated",
        "workout skipped",
        "workout adaptation",
        "workout-skip",
        "low-confidence-workout",
        "confidence too low",
    ]
    rendered = output.lower()
    assert not [phrase for phrase in forbidden if phrase in rendered]


def test_refresh_injects_request_id_and_replaces_pending_state():
    output = run_app_js(
        ["submitMealV2Refresh", "mealComposerState"],
        """
e.mealComposerState.pending = [{ __v2: true, meal_id: 'meal-1', pendingRefresh: false, items: [], expandedItems: new Set(), save_blocked_item_ids: [] }];
let sent;
sandbox.__fitSet.postMealV2Refresh(async (_id, body) => { sent = body; return { meal_id: 'meal-1', items: [], save_blocked_item_ids: [] }; });
sandbox.__fitSet.renderMealPendingList(() => {});
await e.submitMealV2Refresh('meal-1', { kind: 'add_item', text: 'toast' });
process.stdout.write(JSON.stringify({ kind: sent.kind, text: sent.text, requestId: typeof sent.request_id, pending: e.mealComposerState.pending[0].pendingRefresh }));
""",
        mocks=["postMealV2Refresh", "renderMealPendingList"],
    )
    assert output == {"kind": "add_item", "text": "toast", "requestId": "string", "pending": False}


def test_accept_and_discard_use_live_endpoints_and_remove_entry_on_success():
    output = run_app_js(
        ["acceptMealV2", "discardMealV2", "mealComposerState"],
        """
const calls = [];
e.mealComposerState.pending = [{ __v2: true, meal_id: 'meal-1', pendingRefresh: false, expandedItems: new Set(), save_blocked_item_ids: [], items: [{ item_id: 'i', status: 'included', estimate: { calories: 1 } }] }];
sandbox.__fitSet.api(async (path, options) => { calls.push({ path, method: options && options.method }); return {}; });
sandbox.__fitSet.renderMealPendingList(() => {});
sandbox.__fitSet.refreshMacroCard(() => {});
sandbox.__fitSet.toast(() => {});
await e.acceptMealV2('meal-1');
e.mealComposerState.pending = [{ __v2: true, meal_id: 'meal-2', pendingRefresh: false, expandedItems: new Set(), save_blocked_item_ids: [], items: [{ item_id: 'i', status: 'included' }] }];
await e.discardMealV2('meal-2');
process.stdout.write(JSON.stringify({ calls, pending: e.mealComposerState.pending.map((item) => item.meal_id) }));
""",
        mocks=["api", "renderMealPendingList", "refreshMacroCard", "toast"],
    )
    assert output["calls"] == [
        {"path": "/api/meal-intake/meal-1/accept", "method": "POST"},
        {"path": "/api/meal-intake/meal-2", "method": "DELETE"},
    ]
    assert output["pending"] == []


def test_rendered_review_controls_drive_accept_discard_and_all_refresh_handlers():
    output = run_app_js(
        ["buildMealReviewCardV2", "mealComposerState"],
        """
function control(value = '') {
  return { value, hidden: false, disabled: false, handlers: {}, attrs: {},
    addEventListener(name, handler) { this.handlers[name] = handler; },
    getAttribute(name) { return this.attrs[name] || null; },
    setAttribute(name, value) { this.attrs[name] = value; }, focus() {} };
}
function renderedRow() {
  const expand = control();
  const mealType = control('dinner');
  const save = control();
  const discard = control();
  const followupInput = control('half cup');
  const followupDismiss = control();
  const followupForm = control();
  followupForm.querySelector = (selector) => selector.includes('followup-answer') ? followupInput : selector.includes('followup-dismiss') ? followupDismiss : null;
  const addInput = control('banana');
  const addForm = control();
  addForm.querySelector = (selector) => selector.includes('add-item-text') ? addInput : null;
  const candidate = control();
  candidate.attrs['data-candidate-id'] = 'candidate-1';
  const portionInput = control('two cups');
  const portionForm = control();
  portionForm.hidden = true;
  portionForm.querySelector = (selector) => selector.includes('portion-text') ? portionInput : null;
  const portionOpen = control();
  const portionCancel = control();
  const skip = control();
  const del = control();
  const restore = control();
  const toggleItem = control();
  const item = {
    getAttribute: (name) => name === 'data-item-id' ? 'item-1' : null,
    querySelector: (selector) => {
      if (selector.includes('toggle-item')) return toggleItem;
      if (selector.includes('portion-edit-form')) return portionForm;
      if (selector.includes('portion-edit-open')) return portionOpen;
      if (selector.includes('portion-edit-cancel')) return portionCancel;
      if (selector.includes('skip-item')) return skip;
      if (selector.includes('delete-item')) return del;
      if (selector.includes('restore-item')) return restore;
      return null;
    },
    querySelectorAll: (selector) => selector.includes('choose-candidate') ? [candidate] : [],
  };
  const row = {
    classList: { add() {} }, attrs: {}, innerHTML: '',
    setAttribute(name, value) { this.attrs[name] = value; },
    querySelector: (selector) => {
      if (selector.includes('toggle-expand')) return expand;
      if (selector.includes('set-meal-type')) return mealType;
      if (selector.includes('data-action="save"')) return save;
      if (selector.includes('data-action="discard"')) return discard;
      if (selector.includes('discard-log')) return null;
      if (selector.includes('followup-form')) return followupForm;
      if (selector.includes('add-item-form')) return addForm;
      return null;
    },
    querySelectorAll: (selector) => selector === '.meal-review-v2-item' ? [item] : [],
  };
  return { row, expand, mealType, save, discard, followupInput, followupDismiss, followupForm, addInput, addForm, candidate, portionInput, portionForm, portionOpen, portionCancel, skip, del, restore, toggleItem, item };
}
function entry(mealId, blocked = []) {
  return {
    __v2: true, meal_id: mealId, meal_type: 'dinner', meal_totals: { calories: 500 },
    local_timestamp: '2026-07-16T18:00:00', local_date: '2026-07-16', local_iso: '2026-07-16T18:00:00-05:00',
    followup: { available: true, question: 'How much?', used: false }, lastFollowupAnswered: false,
    save_blocked_item_ids: blocked, expandedItems: new Set(), pendingRefresh: false,
    items: [
      { item_id: 'item-1', name: 'Rice', status: 'included', estimate: { calories: 400, ambiguous: false, source: 'usda' }, candidates: [{ candidate_id: 'candidate-1', name: 'Verified rice', source_backed: true }] },
      { item_id: 'item-2', name: 'Sauce', status: 'skipped', original_estimate: { calories: 100, ambiguous: true, source: 'ai' }, candidates: [] },
    ],
  };
}
const apiCalls = [];
const refreshCalls = [];
const toastCalls = [];
sandbox.__fitSet.api(async (path, options) => { apiCalls.push({ path, method: options && options.method, body: options && options.body ? JSON.parse(options.body) : null }); return {}; });
sandbox.__fitSet.submitMealV2Refresh(async (mealId, body) => { refreshCalls.push({ mealId, body }); });
sandbox.__fitSet.renderMealPendingList(() => {});
sandbox.__fitSet.refreshMacroCard(() => {});
sandbox.__fitSet.toast((message, tone) => toastCalls.push({ message, tone }));
let active;
sandbox.document.createElement = () => active.row;

const saveEntry = entry('meal-save');
e.mealComposerState.pending = [saveEntry];
active = renderedRow();
const saveRow = e.buildMealReviewCardV2(saveEntry);
await saveRow.querySelector('[data-action="save"]').handlers.click();

const blockedEntry = entry('meal-blocked', ['item-1']);
e.mealComposerState.pending = [blockedEntry];
active = renderedRow();
const blockedRow = e.buildMealReviewCardV2(blockedEntry);
await blockedRow.querySelector('[data-action="save"]').handlers.click();

const discardEntry = entry('meal-discard');
e.mealComposerState.pending = [discardEntry];
active = renderedRow();
const discardRow = e.buildMealReviewCardV2(discardEntry);
await discardRow.querySelector('[data-action="discard"]').handlers.click();
const pendingAfterSaveAndDiscard = e.mealComposerState.pending.map((item) => item.meal_id);

const controlsEntry = entry('meal-controls');
e.mealComposerState.pending = [controlsEntry];
active = renderedRow();
const controls = e.buildMealReviewCardV2(controlsEntry);
active.mealType.handlers.change();
await active.followupForm.handlers.submit({ preventDefault() {} });
active.followupDismiss.handlers.click();
await active.addForm.handlers.submit({ preventDefault() {} });
active.candidate.handlers.click();
active.portionOpen.handlers.click();
await active.portionForm.handlers.submit({ preventDefault() {} });
active.portionCancel.handlers.click();
active.skip.handlers.click();
active.del.handlers.click();
active.restore.handlers.click();
process.stdout.write(JSON.stringify({
  apiCalls, refreshCalls, toasts: toastCalls,
  followupAnswered: controlsEntry.lastFollowupAnswered,
  portion: { formHidden: active.portionForm.hidden, openHidden: active.portionOpen.hidden },
  pendingAfterSaveAndDiscard,
}));
""",
        mocks=["api", "submitMealV2Refresh", "renderMealPendingList", "refreshMacroCard", "toast"],
    )
    assert output["apiCalls"][0]["path"] == "/api/meal-intake/meal-save/accept"
    assert output["apiCalls"][0]["method"] == "POST"
    body = output["apiCalls"][0]["body"]
    assert body["meal_id"] == "meal-save"
    assert body["local_timestamp"] == "2026-07-16T18:00:00"
    assert body["local_date"] == "2026-07-16"
    assert body["local_iso"] == "2026-07-16T18:00:00-05:00"
    assert body["items"][0]["state"] == "included"
    assert body["items"][0]["estimate"] == {"calories": 400, "ambiguous": False, "source": "usda", "meal_type": "dinner"}
    assert body["items"][1]["state"] == "skipped"
    assert body["items"][1]["estimate"] == {"calories": 100, "ambiguous": True, "source": "ai"}
    assert output["apiCalls"][1] == {"path": "/api/meal-intake/meal-discard", "method": "DELETE", "body": None}
    assert output["pendingAfterSaveAndDiscard"] == []
    assert output["refreshCalls"] == [
        {"mealId": "meal-controls", "body": {"kind": "set_meal_type", "meal_type": "dinner"}},
        {"mealId": "meal-controls", "body": {"kind": "followup_answer", "answer": "half cup"}},
        {"mealId": "meal-controls", "body": {"kind": "add_item", "text": "banana"}},
        {"mealId": "meal-controls", "body": {"kind": "choose_candidate", "item_id": "item-1", "candidate_id": "candidate-1"}},
        {"mealId": "meal-controls", "body": {"kind": "edit_portion", "item_id": "item-1", "text": "two cups"}},
        {"mealId": "meal-controls", "body": {"kind": "skip_item", "item_id": "item-1"}},
        {"mealId": "meal-controls", "body": {"kind": "delete_item", "item_id": "item-1"}},
        {"mealId": "meal-controls", "body": {"kind": "restore_item", "item_id": "item-1"}},
    ]
    assert output["followupAnswered"] is True
    assert output["portion"] == {"formHidden": True, "openHidden": False}
    assert any(toast["message"] == "Resolve flagged items before saving." for toast in output["toasts"])


def test_all_removed_meal_renders_discard_log_and_deletes_the_meal():
    output = run_app_js(
        ["buildMealReviewCardV2", "mealComposerState"],
        """
function control() { return { handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; } }; }
const expand = control();
const mealType = control();
const discardLog = control();
const row = {
  innerHTML: '', attrs: {}, classList: { add() {} },
  setAttribute(name, value) { this.attrs[name] = value; },
  querySelector(selector) {
    if (selector.includes('toggle-expand')) return expand;
    if (selector.includes('set-meal-type')) return mealType;
    if (selector.includes('discard-log')) return discardLog;
    return null;
  },
  querySelectorAll: () => [],
};
const entry = {
  __v2: true, meal_id: 'meal-removed', meal_type: 'dinner', meal_totals: {},
  followup: null, save_blocked_item_ids: [], expandedItems: new Set(), pendingRefresh: false,
  items: [
    { item_id: 'skipped', name: 'Rice', status: 'skipped' },
    { item_id: 'deleted', name: 'Sauce', status: 'deleted' },
  ],
};
e.mealComposerState.pending = [entry];
const calls = [];
sandbox.document.createElement = () => row;
sandbox.__fitSet.api(async (path, options) => { calls.push({ path, method: options.method }); return {}; });
sandbox.__fitSet.renderMealPendingList(() => {});
sandbox.__fitSet.refreshMacroCard(() => {});
sandbox.__fitSet.toast(() => {});
const rendered = e.buildMealReviewCardV2(entry);
const html = rendered.innerHTML;
await discardLog.handlers.click();
process.stdout.write(JSON.stringify({
  allRemoved: html.includes('data-state="all-removed"'),
  discardLabel: html.includes('data-action="discard-log">Discard log</button>'),
  saveAction: html.includes('data-action="save"'),
  calls,
  pending: e.mealComposerState.pending.map((item) => item.meal_id),
}));
""",
        mocks=["api", "renderMealPendingList", "refreshMacroCard", "toast"],
    )
    assert output == {
        "allRemoved": True,
        "discardLabel": True,
        "saveAction": False,
        "calls": [{"path": "/api/meal-intake/meal-removed", "method": "DELETE"}],
        "pending": [],
    }


def test_source_viewer_sanitizer_allows_same_origin_routes_only():
    output = run_app_js(
        ["sanitizeMealV2SourceLink"],
        """
sandbox.location.origin = 'https://fitness.local';
sandbox.location.protocol = 'https:';
process.stdout.write(JSON.stringify([
  e.sanitizeMealV2SourceLink('/nutrition/1?x=1'),
  e.sanitizeMealV2SourceLink('https://fitness.local/nutrition/2'),
  e.sanitizeMealV2SourceLink('https://evil.example/x'),
  e.sanitizeMealV2SourceLink('//evil.example/x'),
]));
""",
    )
    assert output == ["/nutrition/1?x=1", "/nutrition/2", "", ""]


def test_rendered_source_control_uses_sanitized_sandboxed_in_app_viewer():
    output = run_app_js(
        ["buildMealReviewV2ItemHtml", "wireMealReviewCardV2"],
        """
sandbox.location.origin = 'https://fitness.local';
sandbox.location.protocol = 'https:';
const appended = [];
function modalNode() {
  return {
    className: '', innerHTML: '', attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    querySelector() { return { focus() {} }; },
    querySelectorAll() { return []; },
    remove() { this.removed = true; },
  };
}
sandbox.document.createElement = () => modalNode();
sandbox.document.body.appendChild = (node) => appended.push(node);
sandbox.__fitSet.focusOpenModal(() => {});
const sourceAttrs = { 'data-source-link': 'https://evil.example/steal', 'data-source-label': 'USDA' };
const sourceBtn = {
  handlers: {},
  addEventListener(name, fn) { this.handlers[name] = fn; },
  getAttribute(name) { return sourceAttrs[name]; },
};
const itemEl = {
  getAttribute: () => 'item-1',
  querySelector(selector) { return selector === '[data-action="open-source"]' ? sourceBtn : null; },
  querySelectorAll: () => [],
};
const toggle = { addEventListener() {} };
const row = {
  querySelector(selector) { return selector === '[data-action="toggle-expand"]' ? toggle : null; },
  querySelectorAll(selector) { return selector === '.meal-review-v2-item' ? [itemEl] : []; },
};
const item = { item_id: 'item-1', name: 'Chicken', status: 'included', source: { label: 'USDA', kind: 'usda', link: 'https://evil.example/steal' } };
const rendered = e.buildMealReviewV2ItemHtml(item, { mealId: 'meal-1' });
e.wireMealReviewCardV2(row, { meal_id: 'meal-1', items: [item], expandedItems: new Set() });
sourceBtn.handlers.click();
const crossOriginModalCount = appended.length;
sourceAttrs['data-source-link'] = '/nutrition/1?source=usda';
sourceBtn.handlers.click();
const modal = appended[0];
process.stdout.write(JSON.stringify({
  renderedControl: rendered.includes('data-action="open-source"'),
  renderedTargetBlank: rendered.includes('target="_blank"'),
  crossOriginModalCount,
  role: modal.attrs.role,
  label: modal.attrs['aria-label'],
  iframeSrc: modal.innerHTML.includes('src="/nutrition/1?source=usda"'),
  iframeSandbox: modal.innerHTML.includes('sandbox="allow-same-origin"'),
  viewerTargetBlank: modal.innerHTML.includes('target="_blank"'),
}));
""",
        mocks=["focusOpenModal"],
    )
    assert output == {
        "renderedControl": True,
        "renderedTargetBlank": False,
        "crossOriginModalCount": 0,
        "role": "dialog",
        "label": "USDA details",
        "iframeSrc": True,
        "iframeSandbox": True,
        "viewerTargetBlank": False,
    }


def test_css_contains_v2_review_styles():
    expected = [
        ".meal-review-v2", ".meal-review-v2-collapsed", ".meal-review-v2-expanded",
        ".meal-review-v2-totals", ".meal-review-v2-kcal", ".meal-review-v2-items",
        ".meal-review-v2-item--removed", ".meal-review-v2-item--blocked",
        ".meal-review-v2-candidate-chip", ".meal-review-v2-source-modal",
        ".meal-review-v2-source-frame", ".meal-review-v2-followup",
    ]
    missing = [selector for selector in expected if selector not in APP_CSS]
    assert not missing, f"FIT-134 V2 styles missing from style.css: {missing}"
