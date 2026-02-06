# Fitness Dashboard — Features

This dashboard is a mobile-first Flask web app (port **5050**) for training tracking + recovery-driven recommendations.

## Core Features

### Dashboard
- Weekly training KPIs (sets, sessions, progression)
- Alerts and training guidance
- Muscle-group volume cards
- Exercise progression cards
- **Recovery Card (Oura)** — at-a-glance recovery metrics (readiness, HRV trend, steps, activity score, RHR, temperature deviation, sleep breakdown)

### Workout Planning
- Next-workout generator (based on history + soreness)
- Smart recommendation summary that blends:
  - Oura readiness
  - HRV trend (7-day)
  - Recent soreness
  - Weather (wttr.in)
  - Time-of-day
  - Recent workout history context ("Last chest: 3d ago")

### Logging
- Workout logging (exercises, sets, RPE, notes)
- Soreness logging (with timestamp; used for time-decayed logic)
- Cardio logging
- Recovery session logging (sauna/cold plunge/etc)

### History
- History view for workouts/cardio/recovery
- Export (markdown)

## API Endpoints

### App / UI
- `GET /` — main app
- `GET /manifest.json` — PWA manifest
- `GET /service-worker.js` — service worker

### Dashboard Data
- `GET /api/dashboard` — primary dashboard payload

### Oura
- `GET /api/oura/status` — today’s Oura snapshot (DB-cached if available)
  - Query: `?refresh=true` forces a live fetch
  - Returns (best-effort):
    - `readiness`, `sleep_score`, `hrv`
    - `steps`, `activity_score`
    - `resting_hr`, `temperature_deviation`
    - `sleep_duration_min`, `sleep_breakdown_min {deep, rem, light, awake}`
- `GET /api/oura/trends` — 7-day HRV trend + cached series

### Weather
- `GET /api/weather` — wttr.in current conditions (cached ~10 min)
  - Query: `?location=San_Antonio`

### Recommendations
- `GET /api/recommendation/smart` — readiness-based intensity suggestion + avoid list

### Logging
- `POST /api/add-workout`
- `POST /api/add-soreness`
- `POST /api/add-cardio`
- `POST /api/add-recovery`

### Settings
- `GET /api/settings`
- `POST /api/settings`

### History
- `GET /api/history`
- `GET /api/history-all`
- `POST /api/delete-history`

(There are additional endpoints for insights/exports/backups in `app.py`; this file lists the commonly used ones.)

## Oura Integration Details

### Data Sources (Oura v2 usercollection)
- `daily_activity` — steps, activity score
- `daily_sleep` — sleep score, HRV, sleep stage durations
- `daily_readiness` — readiness score, resting heart rate, temperature deviation

### Local Storage
- SQLite file: `oura_daily.sqlite3`
- Table: `oura_daily`
  - Automatically **migrates** by `ALTER TABLE ADD COLUMN` when new metrics are introduced
  - Stores a per-day snapshot so the UI remains fast and resilient when Oura is down

### HRV Trend
- A lightweight heuristic using last-3 vs previous-3 average HRV.

## Recommendation Logic (high level)

The smart recommendation starts at `moderate` and adjusts:
- Readiness < 70 → `recovery`
- Readiness > 85 → `intensity`
- HRV trend `declining` nudges intensity down one level
- Extreme heat/cold (wttr) nudges intensity down one level
- Soreness ≥ 6 in last 24h produces an avoid list + split suggestion

## Running

```bash
cd ~/clawd/fitness-dashboard
source venv/bin/activate
python app.py  # or ./start_fitness_dashboard.sh
```

Environment:
- `OURA_API_TOKEN` must be set to enable Oura endpoints.
