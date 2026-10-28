"""calendar_feed: synthetic ICS only, no network."""
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import calendar_feed as cf

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
OPTIONS = [20, 30, 45, 60, 75, 90]
URL = "https://calendar.google.com/calendar/ical/me%40example.test/private-abc123/basic.ics"


def ny(*args):
    return datetime(*args, tzinfo=NY)


def parse(name_or_text, ws, we):
    path = FIXTURES / name_or_text
    text = path.read_text() if path.is_file() else name_or_text
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

    def read(self, n=-1):
        return self.body[:n]

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
