"""FIT-150 pending-refresh UI behavior, exercised through Node runtime."""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def test_pending_refresh_disables_card_edit_controls_but_not_save_or_discard():
    output = run_app_js(
        ["buildMealReviewCardV2", "buildMealReviewV2ItemHtml"],
        """
const wire = () => {};
sandbox.__fitSet.wireMealReviewCardV2(wire);
sandbox.document.createElement = () => ({
  className: '', classList: { add() {} }, setAttribute() {}, querySelector() { return null; },
  querySelectorAll() { return []; }, innerHTML: '',
});
const item = {
  item_id: 'item-1', name: 'Rice', portion: 'small', status: 'included', confidence: 0.9,
  candidates: [{ candidate_id: 'c-1', name: 'Rice', source_backed: true }],
};
const skippedItem = { item_id: 'item-2', name: 'Sauce', status: 'skipped', original_estimate: { calories: 40 }, candidates: [] };
const deletedItem = { item_id: 'item-3', name: 'Dressing', status: 'deleted', original_estimate: { calories: 60 }, candidates: [] };
const entry = {
  __v2: true, meal_id: 'meal-1', meal_type: 'lunch', meal_totals: { calories: 400 },
  followup: { available: true, question: 'Which sauce?', used: false }, save_blocked_item_ids: [],
  items: [item, skippedItem, deletedItem], expandedItems: new Set(), pendingRefresh: true, lastFollowupAnswered: false,
};
const row = e.buildMealReviewCardV2(entry);
const itemHtml = e.buildMealReviewV2ItemHtml(item, { pendingRefresh: true, mealId: 'meal-1' });
const skippedHtml = e.buildMealReviewV2ItemHtml(skippedItem, { pendingRefresh: true, mealId: 'meal-1' });
const deletedHtml = e.buildMealReviewV2ItemHtml(deletedItem, { pendingRefresh: true, mealId: 'meal-1' });
process.stdout.write(JSON.stringify({ card: row.innerHTML, item: itemHtml, skipped: skippedHtml, deleted: deletedHtml }));
""",
        mocks=["wireMealReviewCardV2"],
    )
    card = output["card"]
    item = output["item"]
    skipped = output["skipped"]
    deleted = output["deleted"]

    def opening_tag(html, token):
        token_start = html.index(token)
        tag_start = html.rfind("<", 0, token_start)
        tag_end = html.index(">", token_start)
        return html[tag_start : tag_end + 1]

    followup_answer_tag = opening_tag(card, 'data-field="followup-answer"')
    followup_submit_tag = opening_tag(card, ">Submit</button>")
    followup_dismiss_tag = opening_tag(card, 'data-action="followup-dismiss"')
    add_item_tag = opening_tag(card, 'data-field="add-item-text"')
    add_item_submit_tag = opening_tag(card, ">Add</button>")
    save_tag = opening_tag(card, 'data-action="save"')
    discard_tag = opening_tag(card, 'data-action="discard"')
    choose_candidate_tag = opening_tag(item, 'data-action="choose-candidate"')
    portion_input_tag = opening_tag(item, 'data-field="portion-text"')
    portion_update_tag = opening_tag(item, ">Update</button>")
    skipped_restore_tag = opening_tag(skipped, 'data-action="restore-item"')
    deleted_restore_tag = opening_tag(deleted, 'data-action="restore-item"')

    assert 'data-action="set-meal-type" aria-label="Meal type" disabled' in card
    assert 'required disabled' in followup_answer_tag
    assert "disabled" in followup_submit_tag
    assert 'disabled' in followup_dismiss_tag
    assert 'required disabled' in add_item_tag
    assert "disabled" in add_item_submit_tag
    assert "disabled" not in save_tag
    assert "disabled" not in discard_tag
    assert "disabled" in choose_candidate_tag
    assert "disabled" in portion_input_tag
    assert "disabled" in portion_update_tag
    assert 'meal-review-v2-item--removed' in skipped
    assert 'meal-review-v2-item--removed' in deleted
    assert "disabled" in skipped_restore_tag
    assert "disabled" in deleted_restore_tag
    assert 'data-action="portion-edit-open" disabled' in item
    assert 'data-action="portion-edit-cancel" disabled' in item
    assert 'data-action="skip-item" disabled' in item
    assert 'data-action="delete-item" disabled' in item


def test_submit_refresh_ignores_duplicate_mutation_while_pending():
    output = run_app_js(
        ["submitMealV2Refresh", "mealComposerState"],
        """
e.mealComposerState.pending = [{ __v2: true, meal_id: 'meal-1', pendingRefresh: true, items: [], expandedItems: new Set(), save_blocked_item_ids: [] }];
let calls = 0;
sandbox.__fitSet.postMealV2Refresh(async () => { calls += 1; return {}; });
await e.submitMealV2Refresh('meal-1', { kind: 'add_item', text: 'toast' });
process.stdout.write(JSON.stringify({ calls, pending: e.mealComposerState.pending[0].pendingRefresh }));
""",
        mocks=["postMealV2Refresh"],
    )
    assert output == {"calls": 0, "pending": True}


def test_css_disabled_pointer_events_within_refreshing_card():
    assert ".meal-review-v2--refreshing :disabled" in APP_CSS
    assert "pointer-events: none" in APP_CSS
