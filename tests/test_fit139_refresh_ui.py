from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "js" / "app.js"
STYLE_CSS = ROOT / "static" / "css" / "style.css"


def _block(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def _slice_after(source: str, start: str, length: int) -> str:
    start_index = source.index(start)
    return source[start_index : start_index + length]


def test_refresh_notice_uses_backend_event_api_and_ack_endpoint():
    js = APP_JS.read_text()

    assert "/api/food-log-refresh-events?unacknowledged=true&limit=10" in js
    assert "/api/food-log-refresh-events/${encodeURIComponent(event.id)}/ack" in js
    assert "const foodLogRefreshNoticeState = {" in js
    assert "seen: new Set()" in js
    assert "no client-side" in js
    assert "row diffing" in js


def test_refresh_notice_renders_passive_accessible_toast():
    js = APP_JS.read_text()
    notice_block = _block(js, "function showFoodLogRefreshNotice(event)", "async function fetchFoodLogRefreshNotices")

    assert "food-log-refresh-toast" in notice_block
    assert "role', 'status'" in notice_block
    assert "aria-live', 'polite'" in notice_block
    assert "food-log-refresh-toast-title" in notice_block
    assert "food-log-refresh-toast-detail" in notice_block
    assert "food-log-refresh-toast-dismiss" in notice_block
    assert "dismiss.disabled = true" in notice_block
    assert "foodLogRefreshNoticeState.seen.delete(event.id)" in notice_block


def test_refresh_notice_fetch_is_hooked_to_dashboard_and_history_surfaces():
    js = APP_JS.read_text()
    macro_block = _slice_after(js, "async function refreshMacroCard()", 500)
    history_block = _slice_after(js, "async function renderBodyInterpretationAndNutritionTrend()", 11000)
    boot_block = _slice_after(js, "function boot()", 800)

    assert "fetchFoodLogRefreshNotices().catch" in macro_block
    assert "fetchFoodLogRefreshNotices().catch" in history_block
    assert "fetchFoodLogRefreshNotices().catch" in boot_block


def test_refresh_notice_styles_are_bounded_for_mobile_and_long_food_names():
    css = STYLE_CSS.read_text()
    notice_css = _block(css, ".food-log-refresh-toast {", ".toast-undo {")
    mobile_start = css.rindex("@media (max-width: 480px)", 0, css.index("/* ======================= ACTIVE WORKOUT SET ROWS"))
    mobile_css = css[mobile_start : mobile_start + 260]

    assert "width: min(92vw, 420px)" in notice_css
    assert "max-width: min(92vw, 420px)" in notice_css
    assert "border-radius: 12px" in notice_css
    assert "white-space: normal" in notice_css
    assert "overflow-wrap: anywhere" in notice_css
    assert ".food-log-refresh-toast-dismiss" in notice_css
    assert "width: min(94vw, 420px)" in mobile_css
