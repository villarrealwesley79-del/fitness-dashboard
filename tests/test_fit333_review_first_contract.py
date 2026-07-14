"""FIT-333 review-first contract and UI copy regression tests."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "docs" / "MEAL_INTAKE_CONTRACT.md").read_text(encoding="utf-8")
POLICY = (ROOT / "meal_log_policy.py").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")


def test_contract_declares_fresh_capture_auto_log_is_disabled():
    assert "Auto-log is currently disabled for fresh meal submissions" in CONTRACT
    assert "policy result is advisory" in CONTRACT
    assert '"status": "pending_review"' in CONTRACT


def test_policy_docs_distinguish_theoretical_decision_from_route_behavior():
    policy = POLICY.lower()
    assert "theoretical policy decision" in policy
    assert "text, photo, and barcode routes currently override" in policy
    assert "pending_review" in policy


def test_pending_review_card_says_estimate_does_not_count_until_confirmed():
    start = APP_JS.index("function buildMealReviewCardV2")
    end = APP_JS.index("function buildMealReviewV2ItemHtml", start)
    card = APP_JS[start:end]

    assert '<span class="meal-pending-title">Pending review</span>' in card
    assert "Estimate only — not added to today’s totals until you confirm." in card
    assert 'data-action="save"' in card
    assert 'data-action="portion-edit-open"' not in card
