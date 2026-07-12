"""FIT-313: public landing copy contains only supportable product boundaries."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING_HTML = (ROOT / "templates" / "landing.html").read_text(encoding="utf-8")


def test_landing_replaces_social_proof_with_product_boundaries():
    assert '<section class="product-boundaries"' in LANDING_HTML
    assert (
        '<h2 class="section-title" id="product-boundaries-title">'
        "Product boundaries</h2>"
    ) in LANDING_HTML
    assert "<dl class=\"boundaries-list\">" in LANDING_HTML

    expected_boundaries = {
        "Deployment": "Single-owner",
        "Data": "Local-first",
        "Recommendations": "Deterministic rules with source proof",
        "AI": "Explanation at the edges",
    }
    for label, value in expected_boundaries.items():
        assert f"<dt>{label}</dt>" in LANDING_HTML
        assert f"<dd>{value}</dd>" in LANDING_HTML


def test_landing_does_not_publish_unsourced_metrics_or_testimonials():
    unsupported_claims = (
        "87%",
        "of users hit weekly volume goal",
        "Join fitness-focused people",
        "What People Say",
        "James L.",
        "Maria R.",
        "Tyler C.",
        "★★★★★",
        "AI-powered recommendations",
    )
    for claim in unsupported_claims:
        assert claim not in LANDING_HTML

    assert 'class="social-proof"' not in LANDING_HTML
    assert 'class="stats-band"' not in LANDING_HTML
    assert 'class="testimonials-grid"' not in LANDING_HTML
