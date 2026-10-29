# Google Calendar feed

The daily plan fits around your calendar using Google's private iCal feed. It is read-only: the app never writes to your calendar. Your own assistant can block time through its own calendar connector (see "Using it from your assistant").

## Where the address is

1. Open Google Calendar on a computer (the phone app does not show it).
2. Settings (gear) -> Settings -> in the left list, under "Settings for my calendars", pick the calendar.
3. Scroll to "Integrate calendar" -> copy **Secret address in iCal format**. It looks like `https://calendar.google.com/calendar/ical/<you>/private-<long-token>/basic.ics`.

## Connect it

App -> Settings -> Data sources -> **Google Calendar** -> paste the address -> **Connect**. The row then shows "Connected". **Disconnect** removes the stored address. Anything that is not an `https://calendar.google.com/calendar/ical/.../private-....ics` address is refused.

Under Settings -> Coaching setup, **Training starts** and **Training ends** (default 6:00 AM to 9:00 PM) are the hours the app may schedule a session in.

## It is a secret

Anyone with this address can read every event on that calendar. Do not post it, commit it, or put it in a chat. The app stores it server-side (macOS Keychain, or a `0600` file `.google-calendar-ical-url` in the data folder), never shows it again after saving, and never writes it to logs, error messages or API responses. `GET /api/calendar/ical` returns only `{configured, status, last_fetch}`.

## If it leaks: Reset

In the same "Integrate calendar" section click **Reset** next to the secret address. The old address stops working immediately and Google shows a new one. Paste the new one into the app (the old one shows as "Needs attention" until you do).

## What the app reads

Only timed events. All-day events, cancelled events and events marked "Free" do not block time. Repeating events, single-instance edits and deleted instances are handled. The feed is fetched at most every 15 minutes (10 second timeout, 5 MB cap, 15 second total limit), and a slow or oversized feed is treated as an outage.

## What it changes

- **Today's session length** is the smaller of your preferred duration and the longest free window left today (inside your training hours, from now on), rounded down to a duration option (20, 30, 45, 60, 75 or 90 minutes). The dashboard shows a chip with the window, and "Why this plan?" says when the session budget was cut. The plan generator never builds fewer than two exercises (34 to 60 minutes, by training goal), so the window counts as a fit only when the plan built for it is no longer than the window; otherwise the dashboard says there is no window today and shows your normal plan.
- **Under 20 free minutes** means "no window today": the plan stays as it was and the chip names the first window tomorrow.
- **Outage, bad address or no address:** the normal plan, with no calendar chip. Nothing breaks.

## Using it from your assistant

The MCP server's `get_open_windows(day="today" | "tomorrow")` returns `{status, day, windows: [{start, end, minutes}], busy: [{start, end}]}` from `GET /api/calendar/windows`. `status` is `ok`, `unconfigured` or `error`. Times are ISO 8601 with your local offset, and `windows` are free gaps of at least 20 minutes inside your training hours (today starts from now).

Ask your assistant something like "find me a 45 minute window today and block it as Workout". It reads the free windows through this server and creates the calendar event with its own Google Calendar connector. This server only reads; see `docs/MCP.md`.
