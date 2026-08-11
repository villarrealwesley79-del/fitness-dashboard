"""FIT-219 meal review-card accessibility runtime contracts."""

from __future__ import annotations

from js_runtime import run_app_js


def test_review_cards_are_focusable_groups():
    output = run_app_js(
        ["buildMealReviewCardV2", "buildMealPendingRow"],
        """
const makeNode = () => ({
  attrs: {},
  className: '',
  setAttribute(name, value) { this.attrs[name] = String(value); },
  querySelector: () => ({ addEventListener() {} }),
  querySelectorAll: () => [],
  addEventListener() {},
  classList: { add() {}, remove() {}, contains: () => false },
});
sandbox.document.createElement = () => makeNode();
sandbox.__fitSet.wireMealReviewCardV2(() => {});
const meal = e.buildMealReviewCardV2({
  meal_id: 'meal-1', items: [], meal_totals: {}, expandedItems: new Set(),
  save_blocked_item_ids: [], meal_type: 'dinner',
});
const estimate = e.buildMealPendingRow({
  client_id: 'client-1', estimate: { item_name: 'Rice', confidence: 0.8 }, policy: {},
});
process.stdout.write(JSON.stringify({ meal: meal.attrs, estimate: estimate.attrs }));
""",
        mocks=["wireMealReviewCardV2"],
    )
    assert output == {
        "meal": {
            "data-meal-id": "meal-1",
            "tabindex": "-1",
            "role": "group",
            "aria-label": "Meal to review before saving",
        },
        "estimate": {
            "data-client-id": "client-1",
            "tabindex": "-1",
            "role": "group",
            "aria-label": "Estimate to review before accepting",
        },
    }


def test_focus_is_preserved_across_innerhtml_rerender():
    output = run_app_js(
        ["captureMealPendingFocus", "restoreMealPendingFocus"],
        """
const calls = [];
const target = {
  tagName: 'INPUT', value: 'edited', disabled: false, selectionStart: 2, selectionEnd: 6,
  hasAttribute: (name) => name === 'data-field',
  getAttribute: (name) => name === 'data-field' ? 'item_name' : null,
  closest: () => null,
  focus: () => calls.push('focus'),
  setSelectionRange: (start, end) => calls.push(`selection:${start}:${end}`),
};
const card = {
  getAttribute: (name) => name === 'data-meal-id' ? 'meal-1' : null,
  querySelector: (selector) => selector === '[data-field="item_name"]' ? target : null,
};
target.closest = (selector) => selector === '.meal-pending-row' ? card : null;
const pending = {
  contains: (node) => node === target,
  querySelector: (selector) => selector === '.meal-pending-row[data-meal-id="meal-1"]' ? card : null,
};
sandbox.document.activeElement = target;
const snap = e.captureMealPendingFocus(pending);
target.value = '';
const restored = e.restoreMealPendingFocus(pending, snap);
process.stdout.write(JSON.stringify({ snap, restored, value: target.value, calls }));
""",
    )
    assert output == {
        "snap": {
            "cardKey": 'meal-id="meal-1"',
            "controlSel": '[data-field="item_name"]',
            "itemId": None,
            "selStart": 2,
            "selEnd": 6,
            "value": "edited",
        },
        "restored": True,
        "value": "edited",
        "calls": ["focus", "selection:2:6"],
    }


def test_card_expand_toggle_has_aria_controls_and_expanded():
    output = run_app_js(
        ["buildMealReviewCardV2"],
        """
const row = { attrs: {}, setAttribute(name, value) { this.attrs[name] = String(value); }, className: '', innerHTML: '' };
sandbox.document.createElement = () => row;
sandbox.__fitSet.wireMealReviewCardV2(() => {});
const entry = {
  meal_id: 'meal-42', items: [], meal_totals: {}, expandedItems: new Set(),
  save_blocked_item_ids: [], meal_type: 'lunch',
};
const result = e.buildMealReviewCardV2(entry);
process.stdout.write(JSON.stringify({ attrs: result.attrs, html: result.innerHTML }));
""",
        mocks=["wireMealReviewCardV2"],
    )
    assert output["attrs"]["data-meal-id"] == "meal-42"
    assert 'id="meal-v2-expand-meal-42"' in output["html"]
    assert 'aria-controls="meal-v2-expanded-meal-42"' in output["html"]
    assert 'data-action="toggle-expand" aria-expanded="false"' in output["html"]
    assert 'class="meal-review-v2-expanded" id="meal-v2-expanded-meal-42"' in output["html"]


def test_per_item_toggle_ids_are_meal_namespaced_and_wired():
    output = run_app_js(
        ["buildMealReviewV2ItemHtml"],
        """
const item = { item_id: '7', name: 'Chicken', portion: 'one bowl', status: 'included', confidence: 0.9 };
const html = e.buildMealReviewV2ItemHtml(item, { mealId: 'meal-42', expanded: false });
process.stdout.write(JSON.stringify({ html }));
""",
    )
    assert 'id="meal-v2-item-toggle-meal-42-7"' in output["html"]
    assert 'aria-controls="meal-v2-item-body-meal-42-7"' in output["html"]
    assert 'class="meal-review-v2-item-body" id="meal-v2-item-body-meal-42-7"' in output["html"]
    assert 'data-item-id="7"' in output["html"]


def test_thumbnail_remove_labels_are_distinct():
    output = run_app_js(
        ["renderMealComposerThumbs", "mealComposerState"],
        """
const makeNode = () => ({
  attrs: {}, children: [], classList: { add() {}, remove() {}, contains: () => false },
  setAttribute(name, value) { this.attrs[name] = String(value); },
  appendChild(child) { this.children.push(child); },
  addEventListener() {},
});
sandbox.document.createElement = () => makeNode();
const thumbs = makeNode();
sandbox.elements['meal-composer-thumbs'] = thumbs;
e.mealComposerState.imageFiles = [{ name: 'breakfast.jpg' }, { name: '' }];
e.mealComposerState.imagePreviewUrls = ['blob:one', 'blob:two'];
e.renderMealComposerThumbs();
process.stdout.write(JSON.stringify({
  thumbsHidden: thumbs.hidden,
  images: thumbs.children.map((thumb) => ({ alt: thumb.children[0].alt, label: thumb.children[1].attrs['aria-label'] })),
}));
""",
    )
    assert output == {
        "thumbsHidden": False,
        "images": [
            {"alt": "Attached photo 1 of 2", "label": "Remove photo 1 of 2 (breakfast.jpg)"},
            {"alt": "Attached photo 2 of 2", "label": "Remove photo 2 of 2"},
        ],
    }


def test_confidence_band_is_text_and_aria_label_not_title_only():
    output = run_app_js(
        ["buildMealPendingRow"],
        """
const makeNode = () => ({
  className: '', innerHTML: '', classList: { add() {}, remove() {}, contains: () => false },
  setAttribute() {}, querySelector: () => ({ addEventListener() {} }), querySelectorAll: () => [],
});
sandbox.document.createElement = () => makeNode();
const row = e.buildMealPendingRow({
  client_id: 'client-42',
  estimate: { item_name: 'Rice', confidence: 0.9 },
  policy: { confidence_band: 'high' },
});
process.stdout.write(JSON.stringify({ html: row.innerHTML }));
""",
    )
    assert 'aria-label="Confidence 90% (band: high)"' in output["html"]
    assert 'meal-pending-conf-band' in output["html"]
    assert ' · high' in output["html"]
