"""Google Calendar secret iCal feed -> busy intervals -> free windows -> session fit.

Pure functions, no app wiring. The feed URL is a secret: it is never logged or
echoed in an error message.
"""
from __future__ import annotations

import re
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
        and p.path.startswith("/calendar/ical/")
        and "/private-" in p.path
        and p.path.endswith(".ics")
    ):
        raise ValueError(
            "Use the 'Secret address in iCal format' from Google Calendar settings "
            "(https://calendar.google.com/calendar/ical/.../private-.../basic.ics)."
        )
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # the feed answers directly
        return None


def fetch_ics(url: str, *, timeout: float = 10, max_bytes: int = 5_000_000, opener=None) -> str:
    """Download the feed. Raises ValueError (bad URL, too big) or OSError (network)."""
    url = validate_ical_url(url)
    opener = opener or urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(urllib.request.Request(url), timeout=timeout)
        try:
            data = resp.read(max_bytes + 1)
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
    if rid is not None or "RRULE" not in props:
        starts = [start]
    else:
        ex = {
            _dt(p, v, tz)[0].astimezone(timezone.utc)
            for p, val in props.get("EXDATE", [])
            for v in val.split(",")
            if v.strip()
        }
        rule = rrulestr(props["RRULE"][0][1], dtstart=start)
        starts = [
            s
            for s in islice(takewhile(lambda s: s < we, rule.xafter(ws - dur, inc=True)), _MAX_INSTANCES)
            if s.astimezone(timezone.utc) not in ex and (uid, s.astimezone(timezone.utc)) not in overridden
        ]
    out = []
    for s in starts:
        e = (s.astimezone(timezone.utc) + dur).astimezone(s.tzinfo)
        if s < we and e > ws:
            out.append(Event(uid, s, e, False))
    return out


def parse_events(ics_text: str, window_start: datetime, window_end: datetime) -> list[Event]:
    """Events overlapping the window, recurrences expanded inside it. Never raises on bad ICS.

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
        except (ValueError, KeyError, OverflowError, TypeError):
            continue
        if (uid, rid) not in chosen or seq >= chosen[(uid, rid)][0]:
            chosen[(uid, rid)] = (seq, props)
    overridden = {k for k in chosen if k[1] is not None}  # cancelled overrides still replace
    out: list[Event] = []
    for (uid, rid), (_, props) in chosen.items():
        try:
            out += _instances(uid, rid, props, tz, window_start, window_end, overridden)
        except (ValueError, KeyError, OverflowError, TypeError):
            continue  # shortcut: one bad event (unknown TZID, bad RRULE) is dropped, not reported
    return out


# --- busy / free / fit ---------------------------------------------------------


def busy_intervals(events, window_start: datetime, window_end: datetime) -> list[tuple[datetime, datetime]]:
    """Timed events clipped to the window and merged. All-day events are ignored."""
    clipped = sorted(
        (max(e.start, window_start), min(e.end, window_end))
        for e in events
        if not e.all_day and e.end > window_start and e.start < window_end and e.end > e.start
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
    out, cursor = [], window_start
    for s, e in [*sorted(busy), (window_end, window_end)]:
        if s - cursor >= timedelta(minutes=min_minutes):
            out.append((cursor, s))
        cursor = max(cursor, e)
    return out


def fit_session(windows, wanted_minutes: int, time_options) -> dict:
    """Longest-window fit, rounded down to a time option; fits=False if even the smallest won't."""
    longest = max(windows, key=lambda w: w[1] - w[0], default=None)
    room = int((longest[1] - longest[0]).total_seconds() // 60) if longest else 0
    usable = min(wanted_minutes, room)
    options = [o for o in time_options if o <= usable]
    if not options:
        smallest = min(time_options, default=0)
        return {
            "fits": False, "minutes": 0, "window_start": None, "window_end": None, "shortened": False,
            "reason": f"No free window of at least {smallest} minutes.",
        }
    minutes = max(options)
    start, end = next(w for w in windows if w[1] - w[0] >= timedelta(minutes=minutes))  # earliest that holds it
    shortened = minutes < wanted_minutes
    return {
        "fits": True, "minutes": minutes, "window_start": start, "window_end": end, "shortened": shortened,
        "reason": f"Longest free window is {room} minutes." if shortened else None,
    }
