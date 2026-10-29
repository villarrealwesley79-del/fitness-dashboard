"""PER-1586: get_open_windows passes the dashboard's /api/calendar/windows output through.

The payloads below are the exact shapes tests/test_calendar_routes.py pins for the route
(this venv has no Flask, so the route itself runs in the dashboard suite).
"""
import pytest

from test_server import BOT_TOKEN, ROUTES, call, client, make, seen  # noqa: F401

OK = {
    "status": "ok",
    "day": "today",
    "busy": [{"start": "2026-10-28T12:00-05:00", "end": "2026-10-28T13:00-05:00"}],
    "windows": [
        {"start": "2026-10-28T10:00-05:00", "end": "2026-10-28T12:00-05:00", "minutes": 120},
        {"start": "2026-10-28T13:00-05:00", "end": "2026-10-28T21:00-05:00", "minutes": 480},
    ],
}


@pytest.mark.parametrize(
    "route_output",
    [
        OK,
        {"status": "unconfigured", "day": "today", "windows": [], "busy": []},
        {"status": "error", "day": "today", "windows": [], "busy": []},
    ],
)
def test_open_windows_passes_the_route_output_through(client, seen, monkeypatch, route_output):  # noqa: F811
    monkeypatch.setitem(ROUTES, "/api/calendar/windows", {**route_output, "calendar_url": "private-LEAK"})
    assert call(client, "get_open_windows", BOT_TOKEN, day="today") == route_output
    assert ("GET", "/api/calendar/windows", "Bearer dashboard-read-token-value") in seen


def test_open_windows_asks_the_route_for_the_requested_day(client, monkeypatch):  # noqa: F811
    monkeypatch.setitem(ROUTES, "/api/calendar/windows", {**OK, "day": "tomorrow"})
    assert call(client, "get_open_windows", BOT_TOKEN, day="tomorrow")["day"] == "tomorrow"
