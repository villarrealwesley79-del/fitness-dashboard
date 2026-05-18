# Apple Health → Fitness Dashboard via iOS Shortcut

Instead of installing a third-party app (Health Auto Export), you can build a native iOS Shortcut that reads yesterday's Health data and POSTs it to your dashboard. Pure Apple stack, no App Store purchase.

## What the Shortcut does

Every morning at ~6 AM, it:

1. Computes "yesterday" (a single date value).
2. Reads yesterday's totals for steps, active energy, resting heart rate, HRV, and sleep duration.
3. Reads any workout sessions whose start date was yesterday.
4. Builds a JSON object matching the dashboard's schema.
5. POSTs it to `https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync`.
6. Displays a quick notification with the result.

The backend's dedup key is `(source, record_type, record_date, record_key)` — resending yesterday's data is idempotent. Workouts now include the start timestamp in the key, so AM/PM sessions on the same day don't collapse.

## JSON shape the backend accepts

```json
{
  "steps":          [{"date": "2026-04-22", "value": 8432}],
  "active_energy":  [{"date": "2026-04-22", "value": 612}],
  "heart_rate":     [{"date": "2026-04-22", "value": 52, "type": "resting"}],
  "sleep":          [{"date": "2026-04-22", "duration_minutes": 463, "score": 88}],
  "workouts": [
    {
      "date": "2026-04-22",
      "startDate": "2026-04-22T06:15:00Z",
      "activity_type": "Traditional Strength Training",
      "duration_minutes": 52,
      "total_energy_kcal": 418,
      "avg_heart_rate": 112
    },
    {
      "date": "2026-04-22",
      "startDate": "2026-04-22T17:30:00Z",
      "activity_type": "Walking",
      "duration_minutes": 28,
      "total_energy_kcal": 140,
      "avg_heart_rate": 104
    }
  ]
}
```

Keys the backend recognizes:

| Top-level key    | Unit                                    | Dedup grain |
| ---------------- | --------------------------------------- | ----------- |
| `steps`          | integer count                           | per day     |
| `active_energy`  | kcal                                    | per day     |
| `heart_rate`     | bpm (resting or avg)                    | per day     |
| `sleep`          | minutes (+ optional score)              | per day     |
| `workouts`       | array of sessions with `startDate`      | per workout |

Any of those arrays can be empty or absent. Send what you have.

## Building the Shortcut on iPhone

Open the **Shortcuts** app → **+** to make a new Shortcut, then add these Actions in order:

1. **Get Current Date** — source of "today"
2. **Adjust Date** — subtract `1 Day` from the output of the previous action. This is your `yesterday` variable.
3. **Format Date** — `ISO 8601` → **Date Only** → `yyyy-MM-dd`. Save this as a variable called `yDateStr`.
4. **Find Health Sample where** `Type is Step Count`, `Date is between yesterday 00:00 and yesterday 23:59`, **Sum Quantity**. Save output as `steps`.
5. **Find Health Sample where** `Type is Active Energy`, same date range, **Sum Quantity**. Save as `activeEnergy`.
6. **Find Health Sample where** `Type is Resting Heart Rate`, same date range, **Average Quantity**. Save as `rhr`.
7. **Find Health Sample where** `Type is Heart Rate Variability`, same date range, **Average Quantity**. Save as `hrv`.
8. **Find Health Sample where** `Type is Sleep Analysis`, same date range, **Sum Duration (minutes)**. Save as `sleepMin`.
9. **Find Workouts where** `Date is between yesterday 00:00 and yesterday 23:59`. Save as `workouts`.
10. **Repeat with Each** `workouts`:
    - Inside the loop, **Get Dictionary from Input** and build: `{date: yDateStr, startDate: workout.startDate, activity_type: workout.type, duration_minutes: workout.duration, total_energy_kcal: workout.totalEnergyBurned, avg_heart_rate: workout.averageHeartRate}`
    - **Add to Variable** `workoutArr`.
11. **Dictionary** — build the top-level body:
    ```
    steps:         [{date: yDateStr, value: steps}]
    active_energy: [{date: yDateStr, value: activeEnergy}]
    heart_rate:    [{date: yDateStr, value: rhr, type: "resting"}]
    sleep:         [{date: yDateStr, duration_minutes: sleepMin}]
    workouts:      workoutArr
    ```
12. **Get Contents of URL**
    - URL: `https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync`
    - Method: **POST**
    - Headers: `Content-Type: application/json`
    - Request Body: **JSON** → the Dictionary from step 11.
13. **Show Notification** → include `inserted` and `skipped` from the response so a failed push is visible.

### Automation schedule

- Shortcuts app → **Automation** tab → **+** → **Time of Day** → `6:00 AM daily` → **Run Shortcut** → pick the one you just built → **Run Immediately** (no "ask before running").

This fires once a day before you're awake. The request is tiny (~1 KB) and re-sending yesterday is harmless.

## Manual test (today, right now)

To verify the endpoint accepts your body before wiring the Shortcut, run this on your Mac:

```bash
curl -sk \
  -X POST https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync \
  -H "Content-Type: application/json" \
  -b /tmp/fd-cookies.txt \
  -d '{
    "steps":         [{"date": "2026-04-22", "value": 8432}],
    "active_energy": [{"date": "2026-04-22", "value": 612}],
    "heart_rate":    [{"date": "2026-04-22", "value": 52, "type": "resting"}],
    "sleep":         [{"date": "2026-04-22", "duration_minutes": 463, "score": 88}],
    "workouts": [
      {"date": "2026-04-22", "startDate": "2026-04-22T06:15:00Z", "activity_type": "Traditional Strength Training", "duration_minutes": 52, "total_energy_kcal": 418, "avg_heart_rate": 112},
      {"date": "2026-04-22", "startDate": "2026-04-22T17:30:00Z", "activity_type": "Walking", "duration_minutes": 28, "total_energy_kcal": 140, "avg_heart_rate": 104}
    ]
  }'
```

Expected response:

```json
{"status": "ok", "inserted": 6, "skipped": 0, "sync_token": "2026-04-23T..."}
```

Re-run it: `inserted` stays 6, nothing duplicates. Change the second workout's `startDate` and it'll accept a 7th row (the new AM/PM dedup fix).

## Storage math

- Per day: 1 steps + 1 active_energy + 1 heart_rate + 1 sleep + N workouts ≈ 5–7 rows × ~1 KB each.
- Per year: ~2 MB. Ten years: ~20 MB. No pruning needed in practice.
- DB file: `apple_health_sync.db`.

## Auth note

The sync endpoint is on the public-prefix allowlist (see `auth.py`) so Shortcuts can POST without a session cookie. If you ever want to lock it down, add a `?token=<secret>` query param — the backend already reads `X-Sync-Token` and `token=` but doesn't require them today. Once you add a token, update the Shortcut's URL or header.
