"""FIT-219 meal review-card accessibility contract tests.

Grep-style contract guards (repo convention: see test_fit142_barcode_ui.py /
test_fit192_accessibility_contract.py) for the focus-management + ARIA wiring
added to the meal/food review cards: focus targets on each card, aria-controls/
aria-expanded on the expand toggles, globally-unique per-item ids, distinct
per-thumbnail remove labels, and the confidence band surfaced as text + an
aria-label rather than a title-only tooltip.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")


def test_review_cards_are_focusable_groups():
    # Both the legacy (buildMealPendingRow) and V2 (buildMealReviewCardV2) cards
    # become programmatic focus targets (tabindex=-1) and labelled groups so
    # renderMealPendingList() can move keyboard focus to a freshly created card.
    assert APP_JS.count("row.setAttribute('tabindex', '-1');") >= 2
    assert APP_JS.count("row.setAttribute('role', 'group');") >= 2
    assert "row.setAttribute('aria-label', 'Estimate to review before accepting');" in APP_JS
    assert "row.setAttribute('aria-label', 'Meal to review before saving');" in APP_JS


def test_focus_is_preserved_across_innerhtml_rerender():
    # The list is rebuilt via innerHTML on every interaction; focus + caret must
    # be captured before and restored after, and not stolen on first hydration.
    assert "function captureMealPendingFocus(pendingList)" in APP_JS
    assert "function restoreMealPendingFocus(pendingList, snap)" in APP_JS
    assert "let mealPendingFirstRenderDone = false;" in APP_JS


def test_card_expand_toggle_has_aria_controls_and_expanded():
    assert 'id="meal-v2-expand-${escapeHtml(entry.meal_id)}"' in APP_JS
    assert 'aria-controls="meal-v2-expanded-${escapeHtml(entry.meal_id)}"' in APP_JS
    assert 'data-action="toggle-expand" aria-expanded="${expanded}"' in APP_JS
    # The controlled region carries the matching id.
    assert 'class="meal-review-v2-expanded" id="meal-v2-expanded-${escapeHtml(entry.meal_id)}"' in APP_JS


def test_per_item_toggle_ids_are_meal_namespaced_and_wired():
    # item_id is only unique within a meal, so the toggle/body ids are namespaced
    # by meal id to stay globally unique across multiple cards on the page.
    assert "const itemKey = `${opts.mealId || 'meal'}-${item.item_id}`;" in APP_JS
    assert 'id="meal-v2-item-toggle-${escapeHtml(itemKey)}"' in APP_JS
    assert 'aria-controls="meal-v2-item-body-${escapeHtml(itemKey)}"' in APP_JS
    assert 'class="meal-review-v2-item-body" id="meal-v2-item-body-${escapeHtml(itemKey)}"' in APP_JS


def test_thumbnail_remove_labels_are_distinct():
    # Each remove button is individually identifiable (position + total + file
    # name) instead of a shared generic "Remove photo N".
    assert "const fileName = (file && file.name) ? ` (${file.name})` : '';" in APP_JS
    assert "remove.setAttribute('aria-label', `Remove photo ${i + 1} of ${count}${fileName}`);" in APP_JS


def test_confidence_band_is_text_and_aria_label_not_title_only():
    # The legacy confidence chip exposes the band as visible text plus an
    # aria-label, not just a title tooltip.
    conf_line = next(
        line for line in APP_JS.splitlines()
        if 'class="meal-pending-conf"' in line
    )
    assert 'aria-label="${confTitle}"' in conf_line
    assert "meal-pending-conf-band" in conf_line
    assert "policy.confidence_band" in conf_line
