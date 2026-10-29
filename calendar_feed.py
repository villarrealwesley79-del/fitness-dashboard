"""Google Calendar secret iCal feed -> busy intervals -> free windows -> session fit.

Pure functions, no app wiring. The feed URL is a secret: it is never logged or
echoed in an error message.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from itertools import islice, takewhile
from typing import NamedTuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

_HOST = "calendar.google.com"
_MAX_INSTANCES = 1000  # per recurring event inside the window
_FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # most days each month can have
_DURATION = re.compile(
    r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


class Event(NamedTuple):
    uid: str
    start: datetime  # timezone-aware
    end: datetime  # timezone-aware
    all_day: bool


def validate_ical_url(url: str) -> str:
    """Return the stripped URL if it is a Google secret iCal address, else ValueError."""
    if not isinstance(url, str):
        raise ValueError("Calendar address must be text.")
    url = url.strip()
    p = urlsplit(url)
    if not (
        url.isascii()
        and url.isprintable()
        and " " not in url
        and "?" not in url
        and "#" not in url
        and p.scheme == "https"
        and p.netloc == _HOST  # rejects userinfo, ports and other hosts
        and re.fullmatch(r"/calendar/ical/[^/]+/private-[^/]+/[^/]+\.ics", p.path)
    ):
        raise ValueError(
            "Use the 'Secret address in iCal format' from Google Calendar settings "
            "(https://calendar.google.com/calendar/ical/.../private-.../basic.ics)."
        )
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # the feed answers directly
        return None


def fetch_ics(
    url: str, *, timeout: float = 10, total_timeout: float = 15, max_bytes: int = 5_000_000, opener=None
) -> str:
    """Download the feed. Raises ValueError (bad URL, too big) or OSError (network, too slow).

    `timeout` is per socket operation; `total_timeout` bounds the body read, so a drip-fed feed
    overshoots it by at most one `timeout`. read1 makes one recv per call; read() would wait for the
    whole chunk and never reach the deadline check while bytes trickle in.
    """
    url = validate_ical_url(url)
    opener = opener or urllib.request.build_opener(_NoRedirect)
    data, deadline = b"", time.monotonic() + total_timeout
    try:
        resp = opener.open(urllib.request.Request(url), timeout=timeout)
        try:
            while len(data) <= max_bytes:  # one byte past the cap is enough to refuse
                chunk = getattr(resp, "read1", resp.read)(min(65536, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
                if time.monotonic() > deadline:
                    raise TimeoutError
        finally:
            resp.close()
    except urllib.error.HTTPError as exc:
        raise OSError(f"Calendar feed returned HTTP {exc.code}.") from None
    except Exception:  # URLError/timeouts embed the URL in their text
        raise OSError("Calendar feed could not be fetched.") from None
    if len(data) > max_bytes:
        raise ValueError("Calendar feed is too large.")
    return data.decode("utf-8", errors="replace")


# --- parsing -----------------------------------------------------------------


def _vevents(text: str) -> list[dict]:
    """Unfold lines (RFC 5545 3.1) and return one {NAME: [(params, value)]} per VEVENT."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    events: list[dict] = []
    cur: dict | None = None
    nested = 0  # depth inside VALARM and other sub-components
    for line in lines:
        quoted = False
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == ":" and not quoted:
                break
        else:
            continue
        name, *raw_params = line[:i].split(";")
        name, value = name.upper(), line[i + 1 :]
        if name == "BEGIN":
            if cur is None and value.upper() == "VEVENT":
                cur = {}
            elif cur is not None:
                nested += 1
        elif name == "END":
            if nested:
                nested -= 1
            elif cur is not None and value.upper() == "VEVENT":
                events.append(cur)
                cur = None
        elif cur is not None and not nested:
            params = {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in raw_params)}
            cur.setdefault(name, []).append((params, value))
    return events


def _dt(params: dict, value: str, default_tz) -> tuple[datetime, bool]:
    """Parse an iCal DATE / DATE-TIME into (aware datetime, is_all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=default_tz), True
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
    tz = ZoneInfo(params["TZID"]) if "TZID" in params else default_tz  # floating -> window tz
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=tz), False


def _duration(value: str) -> timedelta:
    m = _DURATION.match(value.strip())
    if not m:
        raise ValueError("bad duration")
    w, d, h, mi, s = (int(g or 0) for g in m.groups()[1:])
    delta = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -delta if m.group(1) == "-" else delta


def _rule_possible(rule_text: str) -> bool:
    """False for rules whose walk from an old DTSTART would be slow, never match, or crash dateutil.

    shortcut: sub-daily rules, dense BYHOUR x BYMINUTE x BYSECOND grids (> 24 times a day) and
    BYEASTER (a dateutil extension that crashes) are dropped, and month/day-of-month pairs that
    cannot exist (Feb 30) are screened. Other impossible rules (BYYEARDAY=366 with BYMONTH=1) walk to
    year 9999 (about 0.2 s each), so parse_events caps the total parse time. Upgrade if a real feed does that.
    """
    parts = dict(p.partition("=")[::2] for p in rule_text.upper().split(";"))
    if parts.get("FREQ") not in _FREQS or int(parts.get("INTERVAL", "1")) < 1 or "BYEASTER" in parts:  # INTERVAL=0 makes dateutil loop forever
        return False
    n = 1
    for k in ("BYHOUR", "BYMINUTE", "BYSECOND"):
        n *= len([v for v in parts.get(k, "").split(",") if v]) or 1
    if n > 24:
        return False
    months = [int(m) for m in parts.get("BYMONTH", "").split(",") if m]
    if any(not 1 <= m <= 12 for m in months):
        return False
    days = [abs(int(d)) for d in parts.get("BYMONTHDAY", "").split(",") if d]
    return not (months and days) or any(d <= _MONTH_DAYS[m - 1] for m in months for d in days)


def _first(props: dict, name: str):
    return props[name][0] if name in props else None


def _instances(uid, rid, props, tz, ws, we, overridden) -> list[Event]:
    status, transp = _first(props, "STATUS"), _first(props, "TRANSP")
    if (status and status[1].strip().upper() == "CANCELLED") or (
        transp and transp[1].strip().upper() == "TRANSPARENT"
    ):
        return []
    start, all_day = _dt(*_first(props, "DTSTART"), tz)
    if "DTEND" in props:
        end = _dt(*_first(props, "DTEND"), tz)[0]
    elif "DURATION" in props:
        end = (start.astimezone(timezone.utc) + _duration(props["DURATION"][0][1])).astimezone(start.tzinfo)
    else:
        end = start + timedelta(days=1) if all_day else start
    if all_day:  # all-day events never block; no recurrence expansion needed
        return [Event(uid, start, end, True)]
    dur = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)  # real elapsed time across DST
    if dur < timedelta(0):  # negative DURATION/DTEND before DTSTART: nothing to block
        return []
    u = lambda d: d.astimezone(timezone.utc)  # same-zone aware datetimes compare by wall clock; UTC is real time
    if rid is not None:
        starts = [start]
    else:
        dates = lambda name: [  # RDATE VALUE=PERIOD is skipped; Google does not emit it
            _dt(p, v, tz)[0]
            for p, val in props.get(name, [])
            if p.get("VALUE") != "PERIOD"
            for v in val.split(",")
            if v.strip()
        ]
        ex = {u(d) for d in dates("EXDATE")}
        starts = [start]
        if "RRULE" in props:
            starts = []
            rule_text = props["RRULE"][0][1].strip()  # a trailing space must not drop the series
            if _rule_possible(rule_text):
                until = lambda m: "UNTIL=" + datetime.strptime(m[1] + (m[2] or "235959"), "%Y%m%d%H%M%S").replace(
                    tzinfo=start.tzinfo).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # dateutil needs UTC UNTIL
                rule = rrulestr(re.sub(r"UNTIL=(\d{8})(?:T(\d{6}))?(?=;|$)", until, rule_text, flags=re.I), dtstart=start)
                starts = list(islice(takewhile(lambda s: u(s) < u(we), rule.xafter(u(ws) - dur, inc=True)), _MAX_INSTANCES))
        starts = [s for s in {u(s): s for s in starts + dates("RDATE")}.values()  # one instance per moment
                  if u(s) not in ex and (uid, u(s)) not in overridden]
    out = []
    for s in starts:
        e = (u(s) + dur).astimezone(s.tzinfo)
        if u(s) < u(we) and u(e) > u(ws):
            out.append(Event(uid, u(s).astimezone(s.tzinfo), e, False))  # a DST-gap wall time becomes the real one
    return out


def parse_events(
    ics_text: str, window_start: datetime, window_end: datetime, *, max_seconds: float = 5
) -> list[Event]:
    """Events overlapping the window, recurrences expanded inside it. Never raises on bad ICS.

    Raises TimeoutError when expanding the feed takes longer than max_seconds (checked between
    events): a partial list would show busy time as free.

    RDATE (DATE / DATE-TIME) adds instances like RRULE does; RDATE;VALUE=PERIOD is skipped.

    window_start must be timezone-aware; floating (zoneless) times take its zone.
    """
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("Window must be timezone-aware.")
    tz = window_start.tzinfo
    chosen: dict[tuple, tuple[int, dict]] = {}  # (uid, recurrence-id) -> (sequence, props)
    for n, props in enumerate(_vevents(ics_text)):
        try:
            uid = _first(props, "UID")
            uid = uid[1].strip() if uid else f"_{n}"
            rid = _first(props, "RECURRENCE-ID")
            rid = _dt(*rid, tz)[0].astimezone(timezone.utc) if rid else None
            seq = _first(props, "SEQUENCE")
            seq = int(seq[1]) if seq else 0
        except Exception:  # per-event isolation is the contract: one bad event never aborts the feed
            continue
        if (uid, rid) not in chosen or seq >= chosen[(uid, rid)][0]:
            chosen[(uid, rid)] = (seq, props)
    overridden = {k for k in chosen if k[1] is not None}  # cancelled overrides still replace
    out: list[Event] = []
    deadline = time.monotonic() + max_seconds
    for (uid, rid), (_, props) in chosen.items():
        if time.monotonic() > deadline:
            raise TimeoutError("Calendar feed took too long to read.")
        try:
            out += _instances(uid, rid, props, tz, window_start, window_end, overridden)
        except Exception:  # per-event isolation is the contract; shortcut: a bad event is dropped, not reported
            continue
    return out


# --- busy / free / fit ---------------------------------------------------------


def busy_intervals(events, window_start: datetime, window_end: datetime) -> list[tuple[datetime, datetime]]:
    """Timed events clipped to the window and merged. All-day events are ignored."""
    u = lambda d: d.astimezone(timezone.utc)  # real time, not wall clock, across DST
    ws, we = u(window_start), u(window_end)
    clipped = sorted(
        (max(u(e.start), ws), min(u(e.end), we))
        for e in events
        if not e.all_day and u(e.end) > ws and u(e.start) < we and u(e.end) > u(e.start)
    )
    merged: list[list[datetime]] = []
    for s, e in clipped:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    tz = window_start.tzinfo
    return [(s.astimezone(tz), e.astimezone(tz)) for s, e in merged]


def free_windows(busy, window_start: datetime, window_end: datetime, min_minutes: int = 20):
    """Gaps between busy intervals inside the window, dropping those shorter than min_minutes.

    For today pass window_start = max(now, training_hours_start) so past slots are not offered.
    """
    u, tz = (lambda d: d.astimezone(timezone.utc)), window_start.tzinfo  # compare in real time across DST
    we = u(window_end)
    out, cursor = [], u(window_start)
    for s, e in [*sorted((u(s), u(e)) for s, e in busy), (we, we)]:
        s = min(s, we)  # a busy interval after the window must not stretch the last gap
        if s - cursor >= timedelta(minutes=min_minutes):
            out.append((cursor.astimezone(tz), s.astimezone(tz)))
        cursor = max(cursor, e)
    return out


def fit_session(windows, wanted_minutes: int, time_options) -> dict:
    """Longest-window fit, rounded down to a time option; fits=False if even the smallest won't."""
    wanted_minutes = max(wanted_minutes, min(time_options, default=0))  # below the smallest option -> smallest
    span = lambda w: w[1].astimezone(timezone.utc) - w[0].astimezone(timezone.utc)  # real elapsed time
    longest = max(windows, key=span, default=None)
    room = int(span(longest).total_seconds() // 60) if longest else 0
    usable = min(wanted_minutes, room)
    options = [o for o in time_options if o <= usable]
    if not options:
        smallest = min(time_options, default=0)
        return {
            "fits": False, "minutes": 0, "window_start": None, "window_end": None, "shortened": False,
            "reason": f"No free window of at least {smallest} minutes.",
        }
    minutes = max(options)
    start, end = next(w for w in windows if span(w) >= timedelta(minutes=minutes))  # earliest that holds it
    shortened = room < wanted_minutes  # only the calendar shortens; rounding down to an option does not
    return {
        "fits": True, "minutes": minutes, "window_start": start, "window_end": end, "shortened": shortened,
        "reason": f"Longest free window is {room} minutes." if shortened else None,
    }
