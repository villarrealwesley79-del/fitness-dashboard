"""PER-1234: the Daily Rhythm visual system stays flat, calm and accessible."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/style.css").read_text() + (ROOT / "static/css/app.css").read_text()
INDEX = (ROOT / "templates/index.html").read_text()
LOGIN = (ROOT / "templates/login.html").read_text()


def test_no_decorative_effects():
    for source in (CSS, LOGIN):
        for banned in ("linear-gradient(", "radial-gradient(", "backdrop-filter", "background-clip: text", "text-fill-color"):
            assert banned not in source, banned


def test_daily_rhythm_tokens_are_defined():
    root = CSS[CSS.index(":root {"):CSS.index("}", CSS.index(":root {"))]
    for token in ("--paper: #f7f1e8", "--card: #ffffff", "--ink: #1c1b1a", "--stone: #4a4741", "--forest: #224228", "--action: #2a72de", "--hairline: #e1dcd3"):
        assert token in root, token


def test_action_blue_is_only_on_the_workout_action():
    rules = re.findall(r"([^{}]+)\{[^}]*var\(--action\)", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
    assert rules
    for selector in rules:
        names = {s.strip().split(":")[0] for s in selector.split(",")}
        assert names <= {"#btn-start-workout", "#btn-start-workout-2", "#btn-adjust-start-workout", "#btn-complete-workout"}, selector


def test_focus_motion_and_hit_area_rules_exist():
    assert ":focus-visible { outline: 2px solid var(--ink)" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert re.search(r"button, select, summary, input[^{]*\{ min-height: 44px; \}", CSS)
    assert ":focus-visible" in LOGIN


def test_navigation_is_word_only_and_keeps_its_hooks():
    assert "tab-ico" not in INDEX
    for tab in ("dashboard", "vitals", "workout", "log", "history", "body", "stats", "settings"):
        assert f'id="tab-btn-{tab}"' in INDEX and f'data-tab="tab-{tab}"' in INDEX
    assert 'role="tablist"' in INDEX


def test_muscle_recovery_levels_stay_distinct():
    backgrounds = [re.search(rf"\.mr-{level}\s*\{{[^}}]*background:\s*([^;]+);", CSS).group(1) for level in ("recovered", "mild", "moderate", "high", "severe")]
    assert len(set(backgrounds)) == 5, backgrounds


def test_selected_wearable_provider_stands_out():
    selected = re.search(r"\.open-wearables-provider-btn\.is-selected\s*\{([^}]*)\}", CSS).group(1)
    assert "border-color: var(--ink)" in selected and "box-shadow: inset 0 0 0 1px var(--ink)" in selected
