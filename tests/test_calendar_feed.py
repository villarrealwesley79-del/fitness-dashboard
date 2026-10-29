"""calendar_feed: synthetic ICS only, no network."""
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import calendar_feed as cf

# Synthetic ICS feeds, inlined because the repo forbids tracked files under tests/fixtures/.
FIXTURES = {
    'dst.ics': """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:daily@example.test
DTSTART;TZID=America/New_York:20260307T090000
DTEND;TZID=America/New_York:20260307T100000
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:overnight@example.test
DTSTART;TZID=America/New_York:20260308T010000
DTEND;TZID=America/New_York:20260308T040000
END:VEVENT
END:VCALENDAR
""",
    'mixed.ics': """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:holiday@example.test
DTSTART;VALUE=DATE:20261006
DTEND;VALUE=DATE:20261007
SUMMARY:All day
END:VEVENT
BEGIN:VEVENT
UID:cancelled@example.test
DTSTART:20261006T150000Z
DTEND:20261006T160000Z
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:free@example.test
DTSTART:20261006T170000Z
DTEND:20261006T180000Z
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:real@example.test
DTSTART:20261006T130000Z
DURATION:PT1H30M
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT10M
DTSTART:20261006T000000Z
END:VALARM
END:VEVENT
END:VCALENDAR
""",
    'weekly.ics': """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//synthetic//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19701101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:sync@example.test
DTSTART;TZID=America/New_York:20261005T090000
DTEND;TZID=America/New_York:20261005T100000
RRULE:FREQ=WEEKLY;BYDAY=MO
EXDATE;TZID=America/New_York:20261019T090000
SUMMARY:Weekly sync
END:VEVENT
BEGIN:VEVENT
UID:sync@example.test
RECURRENCE-ID;TZID=America/New_York:20261012T090000
DTSTART;TZID=America/New_York:20261012T140000
DTEND;TZID=America/New_York:20261012T150000
SEQUENCE:1
SUMMARY:Weekly sync (moved)
END:VEVENT
BEGIN:VEVENT
UID:sync@example.test
RECURRENCE-ID;TZID=America/New_York:20261026T090000
DTSTART;TZID=America/New_York:20261026T090000
DTEND;TZID=America/New_York:20261026T100000
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
""",
}
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
OPTIONS = [20, 30, 45, 60, 75, 90]
URL = "https://calendar.google.com/calendar/ical/me%40example.test/private-abc123/basic.ics"


def ny(*args):
    return datetime(*args, tzinfo=NY)


def parse(name_or_text, ws, we):
    text = FIXTURES.get(name_or_text, name_or_text)
    return cf.parse_events(text, ws, we)


def vevent(*lines):
    return "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


# --- parsing -------------------------------------------------------------------


def test_weekly_rrule_exdate_override_and_cancelled_override():
    events = parse("weekly.ics", ny(2026, 10, 5), ny(2026, 11, 10))
    starts = sorted(e.start.astimezone(NY).replace(tzinfo=None) for e in events)
    # Oct 12 moved to 14:00, Oct 19 EXDATE, Oct 26 cancelled override; Nov 2 is after DST ended.
    assert starts == [
        datetime(2026, 10, 5, 9), datetime(2026, 10, 12, 14), datetime(2026, 11, 2, 9), datetime(2026, 11, 9, 9),
    ]
    assert events[0].end - events[0].start == timedelta(hours=1)
    nov2 = next(e for e in events if e.start.astimezone(NY).day == 2)
    assert nov2.start.astimezone(UTC).hour == 14  # 09:00 EST


def test_recurrence_expansion_is_bounded_by_the_window():
    text = vevent("UID:inf", "DTSTART:20200101T090000Z", "DTEND:20200101T100000Z", "RRULE:FREQ=DAILY")
    events = parse(text, ny(2026, 10, 5), ny(2026, 10, 8))
    assert len(events) == 3


def test_sequence_supersedes_in_either_order():
    old = ["UID:a", "SEQUENCE:0", "DTSTART:20261006T130000Z", "DTEND:20261006T140000Z"]
    new = ["UID:a", "SEQUENCE:2", "DTSTART:20261006T150000Z", "DTEND:20261006T160000Z"]
    for order in ((old, new), (new, old)):
        text = "".join(vevent(*e) for e in order)
        events = parse(text, ny(2026, 10, 6), ny(2026, 10, 7))
        assert [e.start.astimezone(UTC).hour for e in events] == [15]


def test_all_day_cancelled_and_transparent_do_not_block_and_duration_works():
    ws, we = ny(2026, 10, 6), ny(2026, 10, 7)
    events = parse("mixed.ics", ws, we)
    assert any(e.all_day for e in events)
    busy = cf.busy_intervals(events, ws, we)
    assert busy == [(
        datetime(2026, 10, 6, 13, tzinfo=UTC).astimezone(NY), datetime(2026, 10, 6, 14, 30, tzinfo=UTC).astimezone(NY),
    )]  # only the 1h30 DURATION event; VALARM's DTSTART ignored


def test_tzid_and_z_times_agree_and_merge():
    text = vevent("UID:a", "DTSTART;TZID=America/New_York:20261006T090000", "DTEND;TZID=America/New_York:20261006T100000") + vevent(
        "UID:b", "DTSTART:20261006T130000Z", "DTEND:20261006T140000Z"
    )
    ws, we = ny(2026, 10, 6), ny(2026, 10, 7)
    busy = cf.busy_intervals(parse(text, ws, we), ws, we)
    assert busy == [(ny(2026, 10, 6, 9), ny(2026, 10, 6, 10))]


def test_floating_time_uses_window_zone_and_folded_lines_unfold():
    text = vevent(
        "UID:f", "DTSTART;TZID=America/New_\r\n York:20261006T090000", "DTEND:20261006T100000"
    ).replace("\r\n", "\n")  # LF-only feeds are tolerated
    ws, we = ny(2026, 10, 6), ny(2026, 10, 7)
    (event,) = parse(text, ws, we)
    assert event.start == ny(2026, 10, 6, 9) and event.end == ny(2026, 10, 6, 10)


def test_dst_transition_day():
    ws, we = ny(2026, 3, 7), ny(2026, 3, 10)
    events = parse("dst.ics", ws, we)
    daily = sorted((e for e in events if e.uid == "daily@example.test"), key=lambda e: e.start)
    assert [e.start.astimezone(UTC).hour for e in daily] == [14, 13, 13]  # 09:00 local every day
    assert {e.end - e.start for e in daily} == {timedelta(hours=1)}
    overnight = next(e for e in events if e.uid == "overnight@example.test")
    assert overnight.end.astimezone(UTC) - overnight.start.astimezone(UTC) == timedelta(hours=2)  # spring-forward


def test_overnight_event_is_clipped_to_the_window():
    text = vevent("UID:o", "DTSTART;TZID=America/New_York:20261005T220000", "DTEND;TZID=America/New_York:20261006T020000")
    ws, we = ny(2026, 10, 6, 0), ny(2026, 10, 6, 12)
    assert cf.busy_intervals(parse(text, ws, we), ws, we) == [(ny(2026, 10, 6, 0), ny(2026, 10, 6, 2))]
    ws2 = ny(2026, 10, 6, 6)  # training hours start after the event ended
    assert cf.busy_intervals(parse(text, ny(2026, 10, 5), we), ws2, we) == []


@pytest.mark.parametrize(
    "text",
    [
        "", "garbage", "BEGIN:VEVENT", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT\nEND:VCALENDAR",
        vevent("UID:x", "DTSTART:notadate", "DTEND:20261006T100000Z"),
        vevent("UID:x", "DTSTART;TZID=Not/AZone:20261006T090000", "DTEND;TZID=Not/AZone:20261006T100000"),
        vevent("UID:x", "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z", "RRULE:FREQ=NEVER"),
        vevent("UID:x", "DTSTART:20261006T090000Z", "DURATION:garbage"),
        vevent("UID:x", "SEQUENCE:abc", "DTSTART:20261006T090000Z"),
    ],
)
def test_malformed_ics_does_not_crash(text):
    assert parse(text, ny(2026, 10, 6), ny(2026, 10, 7)) == []


def test_one_bad_event_does_not_hide_a_good_one():
    text = vevent("UID:bad", "DTSTART:nope") + vevent("UID:ok", "DTSTART:20261006T130000Z", "DTEND:20261006T140000Z")
    assert [e.uid for e in parse(text, ny(2026, 10, 6), ny(2026, 10, 7))] == ["ok"]


def test_naive_window_is_rejected():
    with pytest.raises(ValueError):
        cf.parse_events("", datetime(2026, 10, 6), datetime(2026, 10, 7))


# --- free windows and fit --------------------------------------------------------


def test_free_windows_from_now_do_not_offer_past_slots_and_drop_short_gaps():
    busy = [(ny(2026, 10, 6, 9), ny(2026, 10, 6, 10)), (ny(2026, 10, 6, 12), ny(2026, 10, 6, 13)),
            (ny(2026, 10, 6, 13, 10), ny(2026, 10, 6, 14))]
    now = ny(2026, 10, 6, 10, 30)  # caller passes max(now, training start)
    assert cf.free_windows(busy, now, ny(2026, 10, 6, 21)) == [
        (ny(2026, 10, 6, 10, 30), ny(2026, 10, 6, 12)),  # the 10-minute gap at 13:00 is dropped
        (ny(2026, 10, 6, 14), ny(2026, 10, 6, 21)),
    ]
    assert cf.free_windows([(ny(2026, 10, 6, 6, 20), ny(2026, 10, 6, 7))], ny(2026, 10, 6, 6), ny(2026, 10, 6, 7)) == [
        (ny(2026, 10, 6, 6), ny(2026, 10, 6, 6, 20))  # exactly 20 minutes is kept
    ]


def test_in_progress_event_pushes_the_window_start():
    # now (10:30) sits inside a meeting that ends 11:00
    busy = cf.busy_intervals(
        [cf.Event("m", ny(2026, 10, 6, 10), ny(2026, 10, 6, 11), False)], ny(2026, 10, 6, 10, 30), ny(2026, 10, 6, 12)
    )
    assert cf.free_windows(busy, ny(2026, 10, 6, 10, 30), ny(2026, 10, 6, 12)) == [(ny(2026, 10, 6, 11), ny(2026, 10, 6, 12))]


def test_fit_session_shortens_to_the_gap():
    windows = [(ny(2026, 10, 6, 12), ny(2026, 10, 6, 12, 45))]
    fit = cf.fit_session(windows, 75, OPTIONS)
    assert (fit["fits"], fit["minutes"], fit["shortened"]) == (True, 45, True)
    assert fit["window_start"] == ny(2026, 10, 6, 12) and fit["window_end"] == ny(2026, 10, 6, 12, 45)
    assert "45" in fit["reason"]


def test_fit_session_rounds_down_and_picks_a_window_that_holds_it():
    windows = [(ny(2026, 10, 6, 8), ny(2026, 10, 6, 8, 30)), (ny(2026, 10, 6, 14), ny(2026, 10, 6, 14, 50))]
    fit = cf.fit_session(windows, 90, OPTIONS)  # longest is 50 -> 45, only the second window holds it
    assert (fit["minutes"], fit["window_start"]) == (45, ny(2026, 10, 6, 14))
    full = cf.fit_session([(ny(2026, 10, 6, 8), ny(2026, 10, 6, 11))], 60, OPTIONS)
    assert (full["minutes"], full["shortened"], full["reason"]) == (60, False, None)


def test_fully_busy_or_too_short_does_not_fit():
    ws, we = ny(2026, 10, 6, 6), ny(2026, 10, 6, 21)
    assert cf.free_windows([(ws, we)], ws, we) == []
    for windows in ([], [(ws, ws + timedelta(minutes=15))]):
        fit = cf.fit_session(windows, 60, OPTIONS)
        assert fit["fits"] is False and fit["minutes"] == 0 and fit["window_start"] is None
        assert "20" in fit["reason"]


def test_free_windows_never_extend_past_window_end():
    out = cf.free_windows([(ny(2026, 10, 6, 22), ny(2026, 10, 6, 23))], ny(2026, 10, 6, 6), ny(2026, 10, 6, 21))
    assert out == [(ny(2026, 10, 6, 6), ny(2026, 10, 6, 21))]


def test_fit_reason_does_not_blame_the_calendar_when_it_has_room():
    win = [(ny(2026, 10, 6, 8), ny(2026, 10, 6, 10))]  # 120 minutes
    fit = cf.fit_session(win, 50, OPTIONS)
    assert (fit["minutes"], fit["shortened"], fit["reason"]) == (45, False, None)  # rounding, not the calendar
    assert cf.fit_session(win, 150, OPTIONS)["shortened"] is True


def test_short_wanted_time_with_a_big_window_is_not_reported_as_no_window():
    fit = cf.fit_session([(ny(2026, 10, 6, 8), ny(2026, 10, 6, 10))], 15, OPTIONS)
    assert (fit["fits"], fit["minutes"], fit["shortened"]) == (True, 20, False)


# --- URL validation and fetch ----------------------------------------------------


def test_valid_url_is_accepted_and_stripped():
    assert cf.validate_ical_url(f"  {URL}\n") == URL


@pytest.mark.parametrize(
    "bad",
    [
        URL.replace("https", "http"),
        URL.replace("calendar.google.com", "evil.example"),
        "https://calendar.google.com.evil.example/calendar/ical/x/private-abc/basic.ics",
        "https://user:pw@calendar.google.com/calendar/ical/x/private-abc/basic.ics",
        "https://calendar.google.com:8443/calendar/ical/x/private-abc/basic.ics",
        "https://calendar.google.com/calendar/ical/x/public/basic.ics",
        "https://calendar.google.com/other/x/private-abc/basic.ics",
        "https://calendar.google.com/calendar/ical/x/private-abc/basic.html",
        "https://calendar.google.com/calendar/ical/x/private-a/../../../x.ics",
        "https://calendar.google.com/calendar/ical/x/public/basic.ics/private-.ics",
        URL + "?next=http://evil.example",
        URL + "?",
        URL + "#frag",
        URL.replace("private-abc123", "private-abc 123"),
        URL.replace("/basic", "\n/basic"),
        "", "not a url", None, 42,
    ],
)
def test_bad_urls_are_rejected_without_echoing_them(bad):
    with pytest.raises(ValueError) as err:
        cf.validate_ical_url(bad)
    if isinstance(bad, str) and bad.strip():
        assert bad.strip() not in str(err.value) and "private-abc" not in str(err.value)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.closed = body, False

    def read(self, n=-1):  # like a socket: each call consumes what it returns
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, body=b"", error=None):
        self.body, self.error, self.calls, self.response = body, error, [], None

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        if self.error:
            raise self.error
        self.response = FakeResponse(self.body)
        return self.response


def test_fetch_ics_returns_text_passes_timeout_and_closes():
    opener = FakeOpener("BEGIN:VCALENDAR é".encode())
    assert cf.fetch_ics(URL, timeout=7, opener=opener) == "BEGIN:VCALENDAR é"
    assert opener.calls == [(URL, 7)] and opener.response.closed


def test_fetch_ics_enforces_the_size_cap():
    assert cf.fetch_ics(URL, max_bytes=10, opener=FakeOpener(b"x" * 10)) == "x" * 10
    with pytest.raises(ValueError, match="too large"):
        cf.fetch_ics(URL, max_bytes=10, opener=FakeOpener(b"x" * 11))


def test_fetch_ics_validates_before_any_request():
    opener = FakeOpener(b"x")
    with pytest.raises(ValueError):
        cf.fetch_ics("http://calendar.google.com/calendar/ical/x/private-abc/basic.ics", opener=opener)
    assert opener.calls == []


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError(f"timed out for {URL}"), TimeoutError(URL), urllib.error.HTTPError(URL, 404, "nf", {}, None)],
)
def test_fetch_errors_never_contain_the_url(error):
    with pytest.raises(OSError) as err:
        cf.fetch_ics(URL, opener=FakeOpener(error=error))
    assert "private-abc123" not in str(err.value) and err.value.__suppress_context__


def test_default_opener_refuses_redirects():
    handler = cf._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


# --- hostile recurrence rules stay cheap ------------------------------------------


def _timed(rrule: str, dtstart: str = "20200101T090000Z") -> str:
    return (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:h\n"
        f"DTSTART:{dtstart}\nDTEND:{dtstart[:9]}100000Z\n{rrule}\nEND:VEVENT\nEND:VCALENDAR\n"
    )


@pytest.mark.parametrize(
    "rrule",
    ["RRULE:FREQ=SECONDLY", "RRULE:FREQ=MINUTELY", "RRULE:FREQ=HOURLY;BYMONTH=2;BYMONTHDAY=30",
     "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", "RRULE:FREQ=YEARLY;BYMONTH=4,6;BYMONTHDAY=31",
     "RRULE:FREQ=DAILY;BYMONTH=13", "RRULE:FREQ=DAILY;INTERVAL=0", "RRULE:FREQ=MONTHLY;INTERVAL=0",
     "RRULE:FREQ=YEARLY;INTERVAL=0", "RRULE:FREQ=WEEKLY;INTERVAL=-1;BYDAY=MO"],
)
def test_sub_daily_and_impossible_rrules_are_dropped_fast(rrule):
    import time

    t = time.monotonic()
    events = cf.parse_events(_timed(rrule), ny(2026, 10, 6), ny(2026, 10, 7))
    assert events == [] and time.monotonic() - t < 0.5


def test_possible_month_day_rule_still_expands():
    events = cf.parse_events(_timed("RRULE:FREQ=MONTHLY;BYMONTHDAY=-1"), ny(2026, 10, 30), ny(2026, 11, 1))
    assert len(events) == 1


def test_negative_duration_event_is_dropped():
    ics = _timed("RRULE:FREQ=DAILY").replace("DTEND:20200101T100000Z", "DURATION:-PT1H")
    assert cf.parse_events(ics, ny(2026, 10, 6), ny(2026, 10, 7)) == []


# --- PR #355 fix round -------------------------------------------------------------


def _busy(text, ws, we):
    return cf.busy_intervals(cf.parse_events(text, ws, we), ws, we)


def test_dst_spring_forward_recurring_overnight_event_overlapping_window_start():
    ics = vevent("UID:night", "DTSTART;TZID=America/New_York:20260301T203000",
                 "DTEND;TZID=America/New_York:20260302T053000", "RRULE:FREQ=DAILY")
    ws, we = ny(2026, 3, 8, 6), ny(2026, 3, 8, 21)
    b = _busy(ics, ws, we)
    assert b and b[0][0] == ws


@pytest.mark.parametrize(
    "rrule",
    ["RRULE:FREQ=DAILY;BYHOUR=" + ",".join(map(str, range(24))) + ";BYMINUTE=" + ",".join(map(str, range(60))),
     "RRULE:FREQ=DAILY;BYHOUR=" + ",".join(map(str, range(24))) + ";BYMINUTE=" + ",".join(map(str, range(60)))
     + ";BYSECOND=" + ",".join(map(str, range(60))),
     "RRULE:FREQ=DAILY;BYEASTER=1"],
)
def test_dense_expansions_and_byeaster_are_dropped_fast(rrule):
    import time

    t = time.monotonic()
    assert cf.parse_events(_timed(rrule, "19900101T090000Z"), ny(2026, 10, 6), ny(2026, 10, 7)) == []
    assert time.monotonic() - t < 1


def test_byeaster_event_does_not_abort_the_feed():
    text = vevent("UID:bad", "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z", "RRULE:FREQ=DAILY;BYEASTER=400") + vevent(
        "UID:ok", "DTSTART:20261006T130000Z", "DTEND:20261006T140000Z")
    assert [e.uid for e in parse(text, ny(2026, 10, 6), ny(2026, 10, 7))] == ["ok"]


def test_any_exception_from_one_event_is_isolated(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dateutil surprise")

    monkeypatch.setattr(cf, "rrulestr", boom)
    text = vevent("UID:bad", "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z", "RRULE:FREQ=DAILY") + vevent(
        "UID:ok", "DTSTART:20261006T130000Z", "DTEND:20261006T140000Z")
    assert [e.uid for e in parse(text, ny(2026, 10, 6), ny(2026, 10, 7))] == ["ok"]


def _mins(w):
    return (w[1].astimezone(UTC) - w[0].astimezone(UTC)).total_seconds() / 60


def test_spring_forward_window_counts_real_elapsed_minutes():
    ws, we = ny(2026, 3, 8, 1, 30), ny(2026, 3, 8, 3, 30)  # 60 real minutes
    wins = cf.free_windows([], ws, we)
    assert [_mins(w) for w in wins] == [60]
    r = cf.fit_session(wins, 90, OPTIONS)
    assert r["minutes"] == 60 and r["shortened"] is True


def test_fall_back_repeated_hour_is_compared_in_real_time():
    ws, we = ny(2026, 11, 1, 0, 30), ny(2026, 11, 1, 3)  # 00:30 EDT -> 03:00 EST, 210 real minutes
    start = datetime(2026, 11, 1, 6, 15, tzinfo=UTC).astimezone(NY)  # second 01:15, EST
    end = datetime(2026, 11, 1, 6, 45, tzinfo=UTC).astimezone(NY)
    b = cf.busy_intervals([cf.Event("x", start, end, False)], ws, we)
    assert [(s.astimezone(UTC).hour * 60 + s.astimezone(UTC).minute, e.astimezone(UTC).hour * 60 + e.astimezone(UTC).minute) for s, e in b] == [(375, 405)]
    assert [_mins(w) for w in cf.free_windows(b, ws, we)] == [105, 75]
    # EDT 01:30 -> EST 01:15 is 45 real minutes though the wall clock runs backwards
    s2 = datetime(2026, 11, 1, 5, 30, tzinfo=UTC).astimezone(NY)
    assert len(cf.busy_intervals([cf.Event("y", s2, start, False)], ws, we)) == 1


def test_rdate_adds_instances_and_exdate_can_remove_them():
    base = ["UID:r", "DTSTART:20261005T090000Z", "DTEND:20261005T100000Z"]
    ws, we = ny(2026, 10, 6), ny(2026, 10, 7)
    b = _busy(vevent(*base, "RDATE:20261006T090000Z"), ws, we)
    assert [(s.astimezone(UTC).hour, e.astimezone(UTC).hour) for s, e in b] == [(9, 10)]
    b = _busy(vevent(*base, "RRULE:FREQ=WEEKLY;COUNT=2", "RDATE:20261006T090000Z"), ws, we)
    assert len(b) == 1
    b = _busy(vevent(*base, "RRULE:FREQ=WEEKLY;COUNT=2", "RDATE:20261006T090000Z", "EXDATE:20261006T090000Z"), ws, we)
    assert b == []


def test_rdate_period_values_are_skipped_not_fatal():
    text = vevent("UID:p", "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z", "RDATE;VALUE=PERIOD:20261007T090000Z/PT1H")
    assert len(parse(text, ny(2026, 10, 6), ny(2026, 10, 7))) == 1


# --- PR #355 fix round 2 -----------------------------------------------------------


@pytest.mark.parametrize("until", ["20261231", "20261231T235959", "20261006"])
def test_non_utc_until_on_timed_series_is_not_dropped(until):
    ics = vevent("UID:u", "DTSTART;TZID=America/New_York:20260105T090000", "DTEND;TZID=America/New_York:20260105T100000",
                 f"RRULE:FREQ=DAILY;UNTIL={until}")
    assert len(cf.parse_events(ics, ny(2026, 10, 6), ny(2026, 10, 7))) == 1  # date-only UNTIL includes that whole day


def test_non_utc_until_still_ends_the_series():
    ics = vevent("UID:u", "DTSTART;TZID=America/New_York:20260105T090000", "DTEND;TZID=America/New_York:20260105T100000",
                 "RRULE:FREQ=DAILY;UNTIL=20261005")
    assert cf.parse_events(ics, ny(2026, 10, 6), ny(2026, 10, 7)) == []


def test_rdate_repeating_an_rrule_instance_yields_one_event():
    ics = vevent("UID:d", "DTSTART:20261005T090000Z", "DTEND:20261005T100000Z", "RRULE:FREQ=DAILY", "RDATE:20261006T090000Z")
    assert len(parse(ics, ny(2026, 10, 6), ny(2026, 10, 7))) == 1


# --- PER-1586 wiring carry-overs -------------------------------------------------


@pytest.mark.parametrize("until", ["20261231", "20261231T235959"])
def test_trailing_space_on_the_rrule_does_not_drop_the_series(until):
    ics = vevent("UID:u", "DTSTART;TZID=America/New_York:20260105T090000", "DTEND;TZID=America/New_York:20260105T100000",
                 f"RRULE:FREQ=DAILY;UNTIL={until} ")
    assert len(cf.parse_events(ics, ny(2026, 10, 6), ny(2026, 10, 7))) == 1
    ics = vevent("UID:u", "DTSTART:20260105T090000Z", "DTEND:20260105T100000Z", "RRULE:FREQ=DAILY ")
    assert len(cf.parse_events(ics, ny(2026, 10, 6), ny(2026, 10, 7))) == 1


def test_event_starting_in_a_dst_gap_is_normalised_to_the_real_wall_time():
    ics = vevent("UID:gap", "DTSTART;TZID=America/New_York:20260301T023000", "DTEND;TZID=America/New_York:20260301T033000",
                 "RRULE:FREQ=WEEKLY;BYDAY=SU")
    (event,) = cf.parse_events(ics, ny(2026, 3, 8), ny(2026, 3, 9))  # 02:30 does not exist on Mar 8
    assert event.start.utcoffset() == timedelta(hours=-4) and event.start.hour == 3 and event.start.minute == 30
    assert event.start.astimezone(UTC) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_parse_time_cap_raises_instead_of_returning_a_partial_feed():
    text = "".join(vevent(f"UID:e{i}", "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z") for i in range(50))
    assert len(cf.parse_events(text, ny(2026, 10, 6), ny(2026, 10, 7))) == 50
    with pytest.raises(TimeoutError):
        cf.parse_events(text, ny(2026, 10, 6), ny(2026, 10, 7), max_seconds=-1)


def test_fetch_total_deadline_stops_a_drip_fed_feed(monkeypatch):
    class Drip:
        def read(self, n=-1):
            return b"x"

        def close(self):
            pass

    class Opener:
        def open(self, request, timeout=None):
            return Drip()

    clock = iter(range(1000))
    monkeypatch.setattr(cf.time, "monotonic", lambda: next(clock))
    with pytest.raises(OSError, match="could not be fetched") as err:
        cf.fetch_ics(URL, total_timeout=5, opener=Opener())
    assert "private-abc123" not in str(err.value)


def test_total_deadline_holds_on_a_real_drip_fed_socket():
    """read() on a real response waits for the whole 64 KiB chunk; read1() lets the deadline fire."""
    import http.server
    import socketserver
    import threading
    import urllib.request

    class Drip(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                for _ in range(40):  # a byte every 0.1 s, then hang up
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    cf.time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Drip)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Local:  # the URL is still validated; the bytes come from the loopback server
        def open(self, request, timeout=None):
            return urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=timeout)

    started = cf.time.monotonic()
    try:
        with pytest.raises(OSError):
            cf.fetch_ics(URL, timeout=1, total_timeout=1, opener=Local())
    finally:
        server.shutdown()
    assert cf.time.monotonic() - started < 2.5  # the server itself would hold on for 4 s
