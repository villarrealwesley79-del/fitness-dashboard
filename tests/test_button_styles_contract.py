from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_btn_secondary_has_dark_theme_styling():
    """FIT-114: `.btn-secondary` must define a background and dark-theme color
    or the meal-detail Correct button reverts to unreadable browser defaults."""
    css = (ROOT / "static" / "css" / "style.css").read_text()

    assert ".btn-secondary {" in css, ".btn-secondary block missing"

    block = css.split(".btn-secondary {", 1)[1].split("}", 1)[0]
    assert "background:" in block, ".btn-secondary missing background rule"
    assert "color: var(--fg-1)" in block, ".btn-secondary must use --fg-1 token"

    assert ".btn-secondary:hover" in css, ".btn-secondary:hover state missing"


def test_meal_detail_correct_button_uses_secondary_class():
    """FIT-114: the meal-detail Correct button must keep .btn-secondary so the
    new style applies."""
    template = (ROOT / "templates" / "index.html").read_text()
    assert 'id="btn-meal-detail-edit"' in template
    assert 'class="btn btn-secondary"' in template
