# Google Calendar feed

The daily plan can fit around your calendar using Google's private iCal feed. It is read-only: the app never writes to your calendar.

> Status: this PR adds only the feed library (`calendar_feed.py`). The Settings field and the storage behaviour described below arrive with the wiring PR.

## Where the address is

1. Open Google Calendar on a computer (the phone app does not show it).
2. Settings (gear) -> Settings -> in the left list, under "Settings for my calendars", pick the calendar.
3. Scroll to "Integrate calendar" -> copy **Secret address in iCal format**. It looks like `https://calendar.google.com/calendar/ical/<you>/private-<long-token>/basic.ics`.

Paste it into the app's Settings. Anything that is not an `https://calendar.google.com/calendar/ical/.../private-....ics` address is refused.

## It is a secret

Anyone with this address can read every event on that calendar. Do not post it, commit it, or put it in a chat. The app stores it server-side, never shows it again after saving, and never writes it to logs or error messages.

## If it leaks: Reset

In the same "Integrate calendar" section click **Reset** next to the secret address. The old address stops working immediately and Google shows a new one. Paste the new one into the app (the old one will be reported as a feed error until you do).

## What the app reads

Only timed events. All-day events, cancelled events and events marked "Free" do not block time. Repeating events, single-instance edits and deleted instances are handled. Work is limited to your training hours.
