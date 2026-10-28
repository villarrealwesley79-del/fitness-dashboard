from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_btn_secondary_has_dark_theme_styling():
    """FIT-114: `.btn-secondary` must define a background and dark-theme color
    or secondary buttons revert to unreadable browser defaults."""
    css = (ROOT / "static" / "css" / "style.css").read_text()

    assert ".btn-secondary {" in css, ".btn-secondary block missing"

    block = css.split(".btn-secondary {", 1)[1].split("}", 1)[0]
    assert "background:" in block, ".btn-secondary missing background rule"
    assert "color: var(--fg-1)" in block, ".btn-secondary must use --fg-1 token"

    assert ".btn-secondary:hover" in css, ".btn-secondary:hover state missing"
