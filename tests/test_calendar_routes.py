"""PER-1586: the calendar feed wired into settings, the plan, the fingerprint and the MCP route.

Synthetic feeds only; the fetch is mocked and production URL validation is never relaxed.
"""
from __future__ import annotations

import importlib
import logging
import os
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

import workout_recommendation_fingerprint as fingerprint_lib
from test_fit256_workout_plan_persistence import _client

URL = "https://calendar.google.com/calendar/ical/me%40example.test/private-abc123secret/basic.ics"
NOW = datetime(2026, 10, 28, 10, 0).astimezone()  # 10:00 local on a day without a DST change
_OMIT_AUTO_CSRF = {"fitness_dashboard.omit_auto_csrf_header": True}


def at(day, hour, minute=0):
    return datetime(2026, 10, 28 + day, hour, minute).astimezone()


def iso(moment):
    return moment.astimezone().isoformat(timespec="minutes")


def ics(*spans):
    """A feed with one timed event per ((day, hour, minute), (day, hour, minute)) span."""
    z = lambda t: at(*t).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events = "".join(
        f"BEGIN:VEVENT\nUID:e{i}@example.test\nDTSTART:{z(a)}\nDTEND:{z(b)}\nEND:VEVENT\n"
        for i, (a, b) in enumerate(spans)
    )
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{events}END:VCALENDAR\n"


# Scenario feeds, "now" is 10:00 and the training hours are 06:00-21:00.
GAP_45 = ics(((0, 10, 45), (0, 21, 0)))  # 45 free minutes now, nothing tomorrow
GAP_10 = ics(((0, 10, 10), (0, 21, 0)))  # 10 free minutes now: too short
BOOKED = ics(((0, 5, 0), (0, 22, 0)), ((1, 5, 0), (1, 22, 0)))  # both days busy


@pytest.fixture
def feed(monkeypatch, tmp_path):
    """(module, client, set_feed): the app with a controllable clock and a mocked fetch."""
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "save_json", lambda *a, **k: None)
    monkeypatch.setattr(module, "_calendar_now", lambda: NOW)
    monkeypatch.setattr(module, "_apply_open_wearables_recommendation_guard", lambda rec, _facts: (rec, {}))
    state = {"body": ics(), "calls": []}

    def fetch(url, **kwargs):
        state["calls"].append(url)
        if isinstance(state["body"], Exception):
            raise state["body"]
        return state["body"]

    set_feed_real = module.calendar_feed.fetch_ics
    monkeypatch.setattr(module.calendar_feed, "fetch_ics", fetch)

    def set_feed(body):
        state["body"] = body

    set_feed.calls, set_feed.real = state["calls"], set_feed_real
    return module, client, set_feed


def connect(client):
    response = client.post("/api/calendar/ical", json={"url": URL})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


# --- /api/calendar/ical ------------------------------------------------------------


def test_ical_starts_unconfigured(feed):
    _, client, _ = feed
    assert client.get("/api/calendar/ical").get_json() == {"configured": False, "status": "unconfigured", "last_fetch": None}


def test_connect_stores_the_address_privately_and_never_returns_it(feed, caplog):
    module, client, _ = feed
    with caplog.at_level(logging.DEBUG):
        posted = connect(client)
        status = client.get("/api/calendar/ical")
    assert posted == {"configured": True, "status": "ok", "last_fetch": iso(NOW)}
    assert status.get_json() == posted
    for text in (str(posted), status.get_data(as_text=True), caplog.text):
        assert "private-" not in text and "abc123secret" not in text
    path = os.environ["GOOGLE_CALENDAR_ICAL_FILE"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert URL not in open(path).read()  # stored encoded, not as plain text
    assert module.load_protected_secret(*module._calendar_secret_args()[0], **module._calendar_secret_args()[1]) == URL


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://calendar.google.com/calendar/ical/me/private-abc123secret/basic.ics"},
        {"url": "https://evil.example/calendar/ical/me/private-abc123secret/basic.ics"},
        {"url": "https://calendar.google.com/calendar/ical/me/public/basic.ics"},
        {"url": URL + "?x=1"},
        {"url": 42},
        {"url": "  "},
        {},
    ],
)
def test_bad_addresses_are_refused_without_echo_or_storage(feed, body):
    _, client, _ = feed
    response = client.post("/api/calendar/ical", json=body)
    assert response.status_code == 400
    assert "abc123secret" not in response.get_data(as_text=True)  # the hint shows the shape, never the input
    assert not os.path.exists(os.environ["GOOGLE_CALENDAR_ICAL_FILE"])
    assert client.get("/api/calendar/ical").get_json()["configured"] is False


def test_post_and_delete_need_the_csrf_header(feed):
    _, client, _ = feed
    for method in (client.post, client.delete):
        response = method("/api/calendar/ical", json={"url": URL}, environ_overrides=_OMIT_AUTO_CSRF)
        assert response.status_code == 403


def test_disconnect_removes_the_address(feed):
    _, client, _ = feed
    connect(client)
    response = client.delete("/api/calendar/ical")
    assert response.get_json() == {"configured": False, "status": "unconfigured", "last_fetch": None}
    assert not os.path.exists(os.environ["GOOGLE_CALENDAR_ICAL_FILE"])
    assert client.get("/api/calendar/windows").get_json()["status"] == "unconfigured"


def test_the_feed_is_fetched_at_most_every_fifteen_minutes(feed):
    module, client, set_feed = feed
    connect(client)
    for _ in range(3):
        client.get("/api/calendar/ical")
        client.get("/api/calendar/windows")
    assert len(set_feed.calls) == 1 and set_feed.calls[0] == URL
    module._CALENDAR["at"] -= module.CALENDAR_REFRESH_SECONDS + 1
    client.get("/api/calendar/ical")
    assert len(set_feed.calls) == 2


# --- /api/calendar/windows (the shape mcp get_open_windows passes through) ----------


def test_windows_route_shape_and_values(feed):
    _, client, set_feed = feed
    set_feed(ics(((0, 12, 0), (0, 13, 0)), ((1, 9, 0), (1, 10, 0))))
    connect(client)
    today = client.get("/api/calendar/windows?day=today").get_json()
    assert today == {
        "status": "ok",
        "day": "today",
        "busy": [{"start": iso(at(0, 12)), "end": iso(at(0, 13))}],
        "windows": [  # starts at now: the morning before 10:00 is not offered
            {"start": iso(NOW), "end": iso(at(0, 12)), "minutes": 120},
            {"start": iso(at(0, 13)), "end": iso(at(0, 21)), "minutes": 480},
        ],
    }
    tomorrow = client.get("/api/calendar/windows?day=tomorrow").get_json()
    assert tomorrow["windows"] == [
        {"start": iso(at(1, 6)), "end": iso(at(1, 9)), "minutes": 180},
        {"start": iso(at(1, 10)), "end": iso(at(1, 21)), "minutes": 660},
    ]
    assert client.get("/api/calendar/windows").get_json() == today  # default day


def test_windows_route_without_a_calendar_or_with_a_bad_day(feed):
    _, client, _ = feed
    assert client.get("/api/calendar/windows?day=tomorrow").get_json() == {
        "status": "unconfigured", "day": "tomorrow", "windows": [], "busy": [],
    }
    assert client.get("/api/calendar/windows?day=yesterday").status_code == 400


def test_training_hours_move_the_window(feed):
    _, client, set_feed = feed
    set_feed(ics())
    connect(client)
    assert client.get("/api/calendar/windows").get_json()["windows"][0]["minutes"] == 660  # 10:00-21:00
    assert client.post("/api/settings", json={"training_hours": {"start": "06:00", "end": "10:30"}}).status_code == 200
    assert client.get("/api/calendar/windows").get_json()["windows"] == [
        {"start": iso(NOW), "end": iso(at(0, 10, 30)), "minutes": 30}
    ]
    client.post("/api/settings", json={"training_hours": {"start": "06:00", "end": "09:00"}})  # day is over
    assert client.get("/api/calendar/windows").get_json()["windows"] == []


# --- training_hours setting ----------------------------------------------------------


def test_training_hours_default_and_validation(feed):
    _, client, _ = feed
    assert client.get("/api/settings").get_json()["training_hours"] == {"start": "06:00", "end": "21:00"}
    ok = client.post("/api/settings", json={"training_hours": {"start": "5:30", "end": "20:00"}})
    assert ok.get_json()["settings"]["training_hours"] == {"start": "05:30", "end": "20:00"}
    for bad in (
        {"start": "21:00", "end": "06:00"}, {"start": "08:00", "end": "08:00"}, {"start": "25:00", "end": "26:00"},
        {"start": "08:00"}, {"start": 8, "end": 9}, "06:00-21:00", None, [],
    ):
        response = client.post("/api/settings", json={"training_hours": bad})
        assert response.status_code == 400, bad
    assert client.get("/api/settings").get_json()["training_hours"] == {"start": "05:30", "end": "20:00"}


def test_a_malformed_stored_value_falls_back_to_the_default(feed):
    module, client, _ = feed
    module.USER_SETTINGS["training_hours"] = {"start": "nope"}
    assert client.get("/api/settings").get_json()["training_hours"] == {"start": "06:00", "end": "21:00"}


# --- schedule_fit in the plan -----------------------------------------------------------


def test_short_gap_shortens_the_plan_and_says_why(feed):
    module, client, set_feed = feed
    set_feed(GAP_45)
    module.USER_SETTINGS["available_time_minutes"] = 75
    connect(client)
    nxt = client.get("/api/next-workout").get_json()
    assert nxt["schedule_fit"] == {
        "status": "ok", "fits": True, "minutes": 45, "shortened": True, "plan_minutes": 45,
        "window_start": iso(NOW), "window_end": iso(at(0, 10, 45)),
        "reason": "Calendar: 45 min fits 10:00 AM to 10:45 AM; session budget set to 45 min",
    }
    assert nxt["next_workout"]["available_time"] == 45


@pytest.mark.parametrize(
    "goal, minutes, fits",
    [("hypertrophy", 20, False), ("hypertrophy", 30, False), ("hypertrophy", 45, True), ("strength", 45, False)],
)
def test_a_window_is_a_fit_only_when_the_plan_built_for_it_fits(feed, goal, minutes, fits):
    """The generator never builds under two exercises (34 min hypertrophy, 60 min strength)."""
    module, client, set_feed = feed
    set_feed(ics(((0, *divmod(10 * 60 + minutes, 60)), (0, 21, 0))))  # busy from `minutes` after 10:00
    module.USER_SETTINGS.update(available_time_minutes=75, training_goal=goal)
    connect(client)
    body = client.get("/api/next-workout").get_json()
    fit, plan = body["schedule_fit"], body["next_workout"]
    assert fit["fits"] is fits
    if fits:
        assert plan["estimated_minutes"] <= minutes
        assert fit["reason"].startswith(f"Calendar: {minutes} min fits")
    else:
        assert fit["plan_minutes"] is None and plan["available_time"] == 75
        assert fit["reason"] == "Calendar: no free window today; next window tomorrow 6:00 AM"


def test_readers_do_not_wait_for_a_calendar_fetch(feed):
    """/gym-now (no calendar of its own) and the dashboard serve the last state while a refresh runs."""
    module, client, set_feed = feed
    set_feed(ics())
    connect(client)
    started, release = threading.Event(), threading.Event()

    def slow(url, **kwargs):
        started.set()
        release.wait(10)
        return ics()

    module.calendar_feed.fetch_ics = slow  # restored by the feed fixture's monkeypatch
    module._CALENDAR["at"] -= module.CALENDAR_REFRESH_SECONDS + 1  # the next reader refetches
    refresher = threading.Thread(target=lambda: module.app.test_client().get("/api/dashboard"), daemon=True)
    refresher.start()
    assert started.wait(10)
    results = []
    reader = threading.Thread(
        target=lambda: results.append((module.app.test_client().get("/gym-now").status_code,
                                       module.app.test_client().get("/api/dashboard").get_json()["schedule_fit"]["status"])),
        daemon=True,
    )
    reader.start()
    reader.join(3)  # the refresh is still blocked: a reader that waited on it would not be done
    finished_early = not reader.is_alive()
    release.set()
    refresher.join(10)
    reader.join(10)
    assert finished_early and results == [(200, "ok")]


def test_free_afternoon_keeps_the_normal_plan(feed):
    module, client, set_feed = feed
    set_feed(ics())
    module.USER_SETTINGS["available_time_minutes"] = 75
    connect(client)
    dash = client.get("/api/dashboard").get_json()
    assert dash["schedule_fit"]["plan_minutes"] is None and dash["schedule_fit"]["shortened"] is False
    assert dash["schedule_fit"]["minutes"] == 75
    assert dash["next_workout"]["available_time"] == 75


def test_dashboard_carries_schedule_fit_and_a_shortened_plan(feed):
    module, client, set_feed = feed
    set_feed(GAP_45)
    module.USER_SETTINGS["available_time_minutes"] = 75
    connect(client)
    dash = client.get("/api/dashboard").get_json()
    assert dash["schedule_fit"]["plan_minutes"] == 45 and dash["next_workout"]["available_time"] == 45


def test_under_twenty_free_minutes_means_next_window_tomorrow(feed):
    module, client, set_feed = feed
    set_feed(GAP_10)
    connect(client)
    fit = client.get("/api/next-workout").get_json()["schedule_fit"]
    assert fit == {
        "status": "ok", "fits": False, "minutes": None, "shortened": False, "plan_minutes": None,
        "next_window": {"day": "tomorrow", "start": iso(at(1, 6))},
        "reason": "Calendar: no free window today; next window tomorrow 6:00 AM",
    }
    assert client.get("/api/next-workout").get_json()["next_workout"]["available_time"] == 60  # the normal plan


def test_fully_booked_days_keep_the_normal_plan_with_a_reason(feed):
    _, client, set_feed = feed
    set_feed(BOOKED)
    connect(client)
    nxt = client.get("/api/next-workout").get_json()
    assert nxt["schedule_fit"]["next_window"] is None and nxt["schedule_fit"]["fits"] is False
    assert nxt["schedule_fit"]["reason"] == "Calendar: no free window today or tomorrow"
    assert nxt["next_workout"]["available_time"] == 60


@pytest.mark.parametrize("failure", [OSError(URL), ValueError(URL), RuntimeError(URL)])
def test_feed_outage_gives_the_normal_plan_and_logs_no_address(feed, caplog, failure):
    module, client, set_feed = feed
    set_feed(failure)
    with caplog.at_level(logging.DEBUG):
        posted = connect(client)
        dash = client.get("/api/dashboard")
        windows = client.get("/api/calendar/windows").get_json()
    assert posted["status"] == "error" and posted["configured"] is True
    assert dash.status_code == 200
    assert dash.get_json()["schedule_fit"] == {"status": "error", "plan_minutes": None}
    assert dash.get_json()["next_workout"]["available_time"] == 60
    assert windows == {"status": "error", "day": "today", "windows": [], "busy": []}
    assert "private-" not in caplog.text and "abc123secret" not in caplog.text
    assert "Calendar feed unavailable" in caplog.text
    assert len(set_feed.calls) == 1  # the outage is cached too


def test_stored_address_that_fails_validation_is_an_error_without_a_request(feed, monkeypatch):
    module, client, set_feed = feed
    monkeypatch.setattr(module.calendar_feed, "fetch_ics", set_feed.real)  # the real validator stops it before any socket
    module.save_protected_secret(
        *module._calendar_secret_args()[0], "http://calendar.google.com/x", **module._calendar_secret_args()[1]
    )
    assert module._calendar_state(NOW, force=True)["status"] == "error"
    assert client.get("/api/dashboard").get_json()["schedule_fit"] == {"status": "error", "plan_minutes": None}


def test_no_calendar_means_no_schedule_change(feed):
    _, client, _ = feed
    assert client.get("/api/dashboard").get_json()["schedule_fit"] == {"status": "unconfigured", "plan_minutes": None}


def test_the_brief_gets_one_calendar_line(feed):
    module, client, set_feed = feed
    set_feed(GAP_45)
    module.USER_SETTINGS["available_time_minutes"] = 75
    connect(client)
    reasoning = client.get("/api/recommendation/smart").get_json()["reasoning"]
    assert reasoning.count("Calendar:") == 1
    assert "Calendar: 45 min fits 10:00 AM to 10:45 AM; session budget set to 45 min" in reasoning


# --- the plan fingerprint -----------------------------------------------------------------


def test_build_fingerprint_changes_with_the_session_length_not_the_window():
    kwargs = dict(
        auth_scope="u", day="2026-10-28", workouts=[], soreness_data=[], cardio_data=[], recovery_data=[],
        user_settings={}, weather_cache={}, open_wearables_configured=False, open_wearables_user_id=None,
        open_wearables_marker={}, wearable_facts=[],
    )
    fit = lambda minutes: {"status": "ok", "plan_minutes": minutes}
    assert fingerprint_lib.build_fingerprint(**kwargs, schedule_fit=fit(45)) != fingerprint_lib.build_fingerprint(**kwargs, schedule_fit=fit(60))
    assert fingerprint_lib.build_fingerprint(**kwargs, schedule_fit=fit(45)) == fingerprint_lib.build_fingerprint(**kwargs, schedule_fit=fit(45))


def test_app_fingerprint_tracks_the_calendar(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "per1586-secret")
    module = importlib.import_module("app")
    monkeypatch.setattr(module, "_calendar_now", lambda: NOW)
    monkeypatch.setitem(module.USER_SETTINGS, "available_time_minutes", 75)
    seen = {}
    real = fingerprint_lib.build_fingerprint
    monkeypatch.setattr(
        module.workout_recommendation_fingerprint, "build_fingerprint",
        lambda **kw: seen.update(kw) or real(**kw),
    )
    prints = []
    args, kwargs = module._calendar_secret_args()
    for body in (None, GAP_45, ics(((0, 11, 0), (0, 21, 0))), GAP_45):
        monkeypatch.setattr(module.calendar_feed, "fetch_ics", lambda url, body=body, **k: body)
        if body is None:
            module.delete_protected_secret(*args, **kwargs)
        else:
            module.save_protected_secret(*args, URL, **kwargs)
        module._calendar_state(NOW, force=True)
        prints.append((module._workout_recommendation_fingerprint(), seen["schedule_fit"]))
    assert [fit for _, fit in prints] == [
        {"status": "unconfigured", "plan_minutes": None},
        {"status": "ok", "plan_minutes": 45},
        {"status": "ok", "plan_minutes": 60},
        {"status": "ok", "plan_minutes": 45},
    ]
    assert len({fp for fp, _ in prints[:3]}) == 3 and prints[1][0] == prints[3][0]
