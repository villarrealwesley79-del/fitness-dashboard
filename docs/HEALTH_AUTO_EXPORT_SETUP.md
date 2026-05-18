# Health Auto Export → Fitness Dashboard — exact tap walkthrough

> Based on the **current** Health Auto Export UI (v2 export format, two-automation model). Verified against the official REST API docs at <https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/> on 2026-04-23.

The backend accepts Health Auto Export's default JSON format natively. No Shortcut, no template editing. Two automations total (metrics + workouts) because HAE's current UI only lets one Data Type per automation.

**Webhook auth (new):** the sync endpoint now requires a shared secret. On the first boot the Flask app generated one and saved it to `.health-sync-token` in the project dir. Get it in any of three ways:

- Dashboard → **Settings → Integrations → Apple Health → Setup** button. The modal shows the full tokenized URL ready to paste.
- `cat /Users/admin/.openclaw/workspace/projects/fitness-dashboard/.health-sync-token` on the Mac mini.
- `curl -sk --cookie YOUR_AUTH_COOKIE https://.../api/apple-health/sync/setup-url` returns the setup URL for the owner-only setup flow. Routine `/sync/status` does not return token material.

Paste the tokenized URL (not the bare one) into every HAE automation.

**The one URL you'll paste (same for both automations):**

```
https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync?token=YOUR_TOKEN_HERE
```

## Defaults to watch out for (field-verified 2026-04-23)

HAE's default values on new automations actively work against this integration. **Every new automation screen needs these manual corrections** before you hit Update:

| Screen / field | App default | What you want | Why it matters |
| --- | --- | --- | --- |
| Automation Enabled toggle | **OFF** | **ON** | Easy to miss — a saved-but-disabled automation looks identical on the list but never fires. |
| Time Grouping | **Year** | **Day** | Year-grouping posts a single giant row covering 365 days — our dedup key eats 364 of them, you lose data. |
| Data Type (new Workouts automation) | **Health Metrics · All Selected** | **Workouts** | Default creates a second Metrics automation, not a Workouts one. |
| Sync Cadence (new automation) | **5 Minutes** | **1 Day** | 5-minute cadence = 288 POSTs/day and burns iOS BGTask budget for nothing. |
| Automation Name | blank / **"New Automation"** | **"Fitness Dashboard — Metrics"** and **"Fitness Dashboard — Workouts"** | Two automations with identical default names are impossible to tell apart on the list. |

Plus the **Export History** sheet that now appears before any Manual Export: it shows a date-range picker (defaults to the last 7 days, e.g. "Apr 17 – Apr 23"). Tap **Begin Export** to proceed. This is new behavior — your doc may mention "Run Now" but the real tap sequence is **Manual Export → pick dates → Begin Export**.

## Step 0 — first-time Health permissions

When you open Health Auto Export for the first time, iOS asks which Health data it can read. Turn **all these on:**

- Steps
- Active Energy
- Resting Heart Rate
- Heart Rate Variability
- Sleep Analysis
- Workouts

If you already dismissed that sheet, re-enable: iPhone **Settings → Health → Data Access & Devices → Health Auto Export → Turn On All**.

## Automation A — Health Metrics (daily aggregates)

In HAE, bottom tab bar → **Automations** → **+**.

| Field | Value |
| --- | --- |
| **Automation Name** | `Fitness Dashboard — Metrics` |
| **Automation Enabled** | **ON** (defaults to OFF — flip it) |
| **Automation Type** | REST API |
| **URL** | `https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync?token=YOUR_TOKEN_HERE` |
| **Export Format** | JSON |
| **Export Version** | **v2** (current format with enhanced data) |
| **Data Type** | **Health Metrics** |
| **Summarize Data** | **ON** (sends daily aggregates — what we want) |
| **Time Grouping** | **Day** (defaults to Year — change it or you'll lose granularity) |
| **Sync Cadence** | Quantity **1** / Interval **Days** (default is 5 Minutes — change it) |
| **Selected Health Metrics** | **Active Energy · Step Count · Resting Heart Rate · Heart Rate Variability · Sleep Analysis** (5 items) |

Tap **Update** / **Save**. Then scroll back to the automation row and tap **Manual Export**. The **Export History** sheet appears with a 7-day date range preset — pick a range that covers the days you want (leave default to test recent data) and tap **Begin Export**.

**Expected result:** green check. If the dashboard's Settings → Integrations → Apple Health row says **"Synced today"** within 5 seconds, you're done with A.

## Automation B — Workouts

Same flow: Automations → **+**. Same URL, same format version, new automation. **Heads up: the new-automation defaults are even worse here.** It starts disabled, with Data Type set to Health Metrics (not Workouts!), and a 5-minute cadence.

| Field | Value |
| --- | --- |
| **Automation Name** | `Fitness Dashboard — Workouts` |
| **Automation Enabled** | **ON** (defaults to OFF) |
| **Automation Type** | REST API |
| **URL** | `https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync?token=YOUR_TOKEN_HERE` |
| **Export Format** | JSON |
| **Export Version** | **v2** |
| **Data Type** | **Workouts** (defaults to Health Metrics · All Selected — change it) |
| **Include Route Data** | **OFF** (GPS polylines bloat the payload; backend ignores them) |
| **Include Workout Metrics** | **OFF** (minute/second-level HR + energy streams — payload balloons, your daily Health Metrics automation already covers the aggregate) |
| **Sync Cadence** | Quantity **1** / Interval **Days** (default is 5 Minutes — critical to change, or you'll burn iOS BGTask budget) |

Save. Fire a Manual Export → pick date range (default last 7 days is fine) → **Begin Export**. You should see a workouts count tick up in `/api/apple-health/sync/status` on the backend.

Note on the exact field labels: the app calls these **Include Route Data** and **Include Workout Metrics** (per the current HAE docs at <https://help.healthyapps.dev/en/health-auto-export/export-format/workouts>). The Workout Metrics toggle, if you turn it on, enables time-series export with Minutes or Seconds granularity — useful if you want per-second HR traces, but our dashboard doesn't consume that today. Leave OFF.

## Why no 6:00 AM picker (and the escape hatch if you want one)

HAE's native **Sync Cadence is interval-based** ("every N days"), not calendar-scheduled. Automations fire when iOS grants background app refresh or when you open the app. In practice: expect a sync once a day, usually sometime in the morning. Not clockwork, but good enough — the webhook is idempotent so an off-hour fire just updates "today's running totals" and re-posting the same day is a no-op thanks to the dedup key.

If you want **deterministic time-of-day firing** (exactly 6:00 AM every morning), the official workaround is an iOS Shortcuts Personal Automation that calls HAE's **Run Automation** action. It's only a three-step Shortcut — much simpler than the full Health-reading Shortcut we skipped. From the HAE docs at <https://help.healthyapps.dev/en/health-auto-export/automations/schedule-automations-using-shortcuts>:

1. Shortcuts app → **Automation** tab → **+** → **Time of Day** → set to 6:00 AM, Daily.
2. Add action → search **"Run Automation"** (provided by HAE) → pick your Health Metrics automation.
3. Enable **Run Immediately** if your iOS version supports it (iOS 15+). Otherwise leave on "Run After Confirmation" — less zero-touch but still works.

Add a second Personal Automation for the Workouts automation if you want both at the same time.

**Caveat:** iOS Personal Automations only fire when the device is unlocked + awake (or when the system decides to wake it). If your iPhone is dead asleep at 6:00, the automation runs when you next wake it. HAE's built-in interval cadence is the same in practice — both rely on iOS's scheduling grace.

If you *don't* want to bother: leave it at `1 Day`. Your manual export already proved data lands correctly, and the staleness watchdog on the Mac will flag >36h gaps.

**Want more syncs per day instead?** Set cadence to `Quantity 12 / Interval Hours` — fires ~twice per day. Storage still negligible.

## Verify on the dashboard

Open [https://admins-mac-mini.tail6c6490.ts.net:5050](https://admins-mac-mini.tail6c6490.ts.net:5050) → **Settings** tab → **Integrations**.

| Row | Expected state after a successful Manual Export |
| --- | --- |
| Apple Health | green dot, chip reads **"Synced today"** |

The chip refreshes when you load the Settings tab. Top-right header button should also show a green dot (AI Coach status — separate signal, but a quick "everything's up" check).

Ground-truth query if the chip lies:

```bash
sqlite3 /Users/admin/.openclaw/workspace/projects/fitness-dashboard/apple_health_sync.db \
  "SELECT record_type, COUNT(*), MAX(created_at) FROM ah_sync_log GROUP BY record_type;"
```

## What actually goes over the wire

**Health Metrics automation (v2, summarize=day):**

```json
{
  "data": {
    "metrics": [
      {"name": "step_count", "units": "count", "data": [{"date": "2026-04-23 00:00:00 -0500", "qty": 8432}]},
      {"name": "active_energy", "units": "kcal", "data": [...]},
      {"name": "resting_heart_rate", "units": "count/min", "data": [...]},
      {"name": "heart_rate_variability", "units": "ms", "data": [...]},
      {"name": "sleep_analysis", "units": "hr", "data": [{"date": "...", "asleep": 7.72, "deep": 1.87, "rem": 2.27, "core": 3.58, "awake": 0.52}]}
    ]
  }
}
```

**Workouts automation:**

```json
{
  "data": {
    "workouts": [
      {"name": "Traditional Strength Training", "start": "2026-04-23 06:15:00 -0500", "end": "...", "duration": 52, "totalEnergy": {"qty": 418, "units": "kcal"}, "avgHeartRate": {"qty": 112, "units": "count/min"}}
    ]
  }
}
```

The backend's `_normalize_hae_payload()` auto-detects both shapes and normalizes to the internal schema (`steps[]`, `active_energy[]`, `heart_rate[]`, `sleep[]`, `workouts[]`) before upsert.

## Dedup keys

| Type | Unique on |
| --- | --- |
| workouts | (source, "workouts", date, start_timestamp) — so AM/PM workouts are separate rows |
| steps | (source, "steps", date, "") — one row per day, resends overwrite |
| active_energy | (source, "active_energy", date, "") — same |
| heart_rate | (source, "heart_rate", date, "") — same |
| sleep | (source, "sleep", date, "") — same |

Resending the same day is harmless. Adding a second AM workout after an earlier PM workout was logged is also harmless — they land as separate rows.

## Troubleshooting

**Manual Export red X with 4xx/5xx on the iPhone.**
- Take a screenshot of the error — the status code tells me exactly what's wrong.
- 5xx typically = backend bug. Check `/tmp/fitness-dashboard.log`.
- 4xx = usually a malformed payload; I'll update the normalizer.

**Manual Export succeeds but `sync/status` unchanged.**
- The automation may have hit a different URL (typo in the paste). Reopen the automation → re-verify the URL reads exactly as above.

**Mac mini asleep when the automation fires.**
- `System Settings → Lock Screen → Turn display off on battery/power: Never`, plus `Energy → Prevent automatic sleep: On`. If the Mac sleeps, the webhook is unreachable until you wake it.

**Duplicate rows after changing a workout's start time in the Watch.**
- Expected — the `record_key` is the start timestamp. If the Watch re-reported with a different minute, the new row is an intentional new record, not a dedup failure. You can cleanup with `DELETE FROM ah_sync_log WHERE record_date = 'YYYY-MM-DD' AND record_type = 'workouts';` and re-sync.

## Staleness watchdog

A launchd agent (`com.fitness-dashboard.staleness`) runs daily at 09:15 and writes to `/tmp/apple-health-staleness.log` if the last sync is > 36 hours old. It stays quiet until the first real sync lands, so it's not noisy during setup.
