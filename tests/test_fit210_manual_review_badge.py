"""FIT-210 manual-review badge contract tests.

A barcode lookup with no verified nutrition source comes back as a manual-
review meal. The prior signal was only a transient toast; FIT-210 adds a
persistent "Manual review" badge to the V2 review card. These grep-style
contract tests (repo convention: see test_fit142_barcode_ui.py /
test_fit192_accessibility_contract.py) guard that the badge string, its CSS,
and its accessible labelling stay shipped.
"""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def test_manual_review_badge_renders_in_v2_review_card():
    output = run_app_js(
        ["buildMealReviewCardV2"],
        """
const makeNode = () => ({
  className: '', innerHTML: '', attrs: {},
  classList: { add() {}, remove() {}, toggle() {} },
  setAttribute(key, value) { this.attrs[key] = value; },
  querySelector() { return null; }, querySelectorAll() { return []; },
});
sandbox.document.createElement = () => makeNode();
sandbox.__fitSet.wireMealReviewCardV2(() => {});
const base = {
  meal_id: 'meal-manual', meal_type: 'lunch', meal_totals: { calories: 500 },
  followup: null, save_blocked_item_ids: [], expandedItems: new Set(),
  pendingRefresh: false, lastFollowupAnswered: false,
  items: [{ item_id: 'item-1', name: 'Unknown barcode', portion: '', status: 'included', confidence: 0.5, source: { kind: 'barcode_pending_source', label: 'Manual' }, candidates: [] }],
};
const manual = e.buildMealReviewCardV2(base);
const verified = e.buildMealReviewCardV2({ ...base, meal_id: 'meal-verified', items: [{ ...base.items[0], source: { kind: 'manual', label: 'Manual' } }] });
process.stdout.write(JSON.stringify({ manual: { attrs: manual.attrs, html: manual.innerHTML }, verified: verified.innerHTML }));
""",
        mocks=["wireMealReviewCardV2"],
    )

    assert output["manual"]["attrs"]["data-source-kind"] == "barcode_pending"
    assert 'class="meal-pending-review-badge"' in output["manual"]["html"]
    assert ">Manual review</span>" in output["manual"]["html"]
    assert 'role="note"' in output["manual"]["html"]
    assert 'aria-label="Manual review' in output["manual"]["html"]
    assert "review and edit the item before saving" in output["manual"]["html"]
    assert "meal-pending-review-badge" not in output["verified"]


def test_manual_review_badge_styles_ship():
    assert ".meal-pending-review-badge {" in APP_CSS
    assert '.meal-pending-row[data-source-kind="barcode_pending"]' in APP_CSS


def test_manual_review_note_is_accessible_not_title_only():
    # The runtime card assertion above verifies the complete accessible badge;
    # keep this test as a concise CSS contract for its persistent visual hook.
    assert ".meal-pending-review-badge" in APP_CSS
