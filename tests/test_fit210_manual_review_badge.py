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


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def test_manual_review_badge_renders_in_v2_review_card():
    # The badge is keyed off the per-item barcode_pending_source kind and is
    # rendered into the live V2 card head (buildMealReviewCardV2), so it
    # survives the V2 backend refresh rather than being a one-shot toast.
    assert "buildMealReviewCardV2" in APP_JS
    assert "source.kind === 'barcode_pending_source'" in APP_JS
    assert 'class="meal-pending-review-badge"' in APP_JS
    assert ">Manual review</span>" in APP_JS
    assert "${reviewBadgeHtml}" in APP_JS


def test_manual_review_badge_styles_ship():
    assert ".meal-pending-review-badge {" in APP_CSS
    assert '.meal-pending-row[data-source-kind="barcode_pending"]' in APP_CSS


def test_manual_review_note_is_accessible_not_title_only():
    # FIT-210 audit nit: the "review manually" note must reach assistive tech
    # via an aria-label (not be buried in a title tooltip, which screen-reader
    # and keyboard users cannot discover).
    badge = APP_JS.split('class="meal-pending-review-badge"', 1)[1].split("</span>", 1)[0]
    assert 'role="note"' in badge
    assert "aria-label=" in badge
    # Accessible name keeps the visible "Manual review" label (Label in Name)
    # and carries the manual-entry explanation.
    assert 'aria-label="Manual review' in badge
    assert "review and edit the item before saving" in badge
