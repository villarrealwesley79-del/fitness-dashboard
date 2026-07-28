"""FIT-283 provider fallback review-card contract tests."""

from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _function_body(name: str, next_name: str) -> str:
    start = APP_JS.find(f"function {name}")
    end = APP_JS.find(f"function {next_name}", start)
    assert start != -1 and end != -1
    return APP_JS[start:end]


def test_candidate_role_normalizer_allowlists_public_values():
    body = _function_body("normalizeVisionCandidateRole", "visionFallbackDetail")

    assert "['primary', 'low_memory', 'fallback']" in body
    assert "return allowed.includes(role) ? role : null" in body


def test_review_detail_is_quiet_and_only_visible_for_non_primary_local_fallbacks():
    body = _function_body("visionFallbackDetail", "isMealV2Payload")

    assert "if (!role || role === 'primary') return ''" in body
    assert "local vision: ${role}" in body
    assert "replace(/_/g, ' ')" in body


def test_v2_review_card_preserves_and_renders_safe_candidate_role_inline():
    normalize = _function_body("normalizeMealV2Entry", "mealV2EntryById")
    renderer = _function_body("buildMealReviewCardV2", "buildMealReviewV2ItemHtml")

    assert "payload.vision && payload.vision.candidate_role" in normalize
    assert "visionFallbackDetail(entry.vision_candidate_role)" in renderer
    assert "meal-review-v2-provider-detail" in renderer
    assert "meal-pending-head" in renderer
    detail_line = next(line for line in renderer.splitlines() if "meal-review-v2-provider-detail" in line)
    assert "badge" not in detail_line.lower()


def test_legacy_review_card_renders_same_inline_detail_without_a_badge():
    renderer = _function_body("buildMealPendingRow", "releasePendingEntryArtifacts")

    assert "visionFallbackDetail(est.vision_candidate_role)" in renderer
    assert "meal-pending-provider-detail" in renderer
    assert "badge" not in renderer.split("visionFallbackDetail(est.vision_candidate_role)", 1)[1][:300].lower()
