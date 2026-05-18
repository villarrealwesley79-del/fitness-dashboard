# Fitness Dashboard — Root Cause & Fix Documentation

**Ticket:** 6bb5f834-e022-4e7a-abe1-000df61ca906
**Date:** 2026-04-13
**Priority:** High
**Status:** FIXED

---

## Root Cause: Dashboard Data Not Showing

### The Problem
After login, the fitness dashboard showed `--` placeholder values for every KPI, metric, and data point. All 9 tabs (dashboard, vitals, workout, log, history, body, analytics, apple-health, settings) rendered their HTML structure correctly but contained zero populated data.

### The Cause
**The frontend JavaScript never called any data-loading API endpoints.**

Specifically:
- `/api/dashboard` — returns headline KPIs, muscle readiness, next workout, body stats, recomp command, nutrition — **never fetched**
- `/api/vitals` — returns weight, heart rate, sleep, activity data with sparkline-ready trend arrays — **never fetched**
- `/api/recommendation/smart` — returns readiness-based workout recommendation — **never fetched**
- `/api/insights` — returns key training insights — **never fetched**
- `/api/adherence` — returns training consistency stats — **never fetched**

Additionally, all form submissions (workout, cardio, soreness, recovery, body measurement) were completely unwired — clicking "Log Set", "Log Cardio", etc. caused standard HTML form submission (page reload) rather than API calls.

### How This Happened
The codebase evolved in two disconnected tracks:
1. **Backend** (app.py) — Rich API endpoints built iteratively, all functional, all returning valid JSON with real data
2. **Frontend** (index.html + app.js) — HTML structure and CSS built, but `app.js` only handled the exercise swap feature and workout modal. No general-purpose data-loading code was ever written.

The only working data fetches in the original code were:
- `/api/oura/sleep-summary` (sleep chart in analytics)
- `/api/apple-health/*` (Apple Health tab, loaded on tab click)
- `/api/history-all` (history tab, loaded via `loadHistory()`)

Everything else was dead HTML.

### Evidence (API Test Results)

All endpoints return 200 with real data:

```
/api/dashboard -> 200 OK  keys=[headline, muscles, exercises, body_stats, recomp_command, next_workout]
  total_sets: 52, improving: 16, avg_readiness: 10.0, current_streak: 0
  latest_weight: 181.4, latest_body_fat: 21.7
  recomp_signal: TRAIN, next_workout_focus: Full Body (11 exercises)

/api/vitals -> 200 OK  keys=[weight, heart_rate, sleep, activity, source]
  weight_current_lbs: 181.4, body_fat_pct: 21.7
  resting_bpm: 44, avg_7d_hours: 6.0
  steps_today: 3467, steps_avg_7d: 3467

/api/recommendation/smart -> 200 OK  keys=[recommendation, readiness, hrv_trend, suggested_workout]
  recommendation: recovery, readiness: 82, hrv_trend: stable

/api/insights -> 200 OK  keys=[insights, charts]
/api/adherence -> 200 OK  keys=[adherence_rate, followed_count, total_recommendations]
/api/oura/status -> 200 OK  keys=[readiness, hrv, resting_hr, activity_score]
/api/apple-health/status -> 200 OK  keys=[available, export_files, health_dir]
```

### The Fix

**Created `/static/js/dashboard.js`** — a 346-line data layer that bridges all frontend elements to their backend APIs:

| Tab | API Called | Elements Populated |
|-----|-----------|-------------------|
| Dashboard | `/api/dashboard`, `/api/recommendation/smart`, `/api/vitals` | Readiness gauge, KPIs, streaks, body stats, workout cards, recovery card, goal banner |
| Vitals | `/api/vitals` | Weight/HR/sleep/activity cards, sparklines, detail charts |
| Analytics | `/api/insights`, `/api/adherence`, `/api/dashboard` | Insights grid, consistency stats, progress/volume charts |
| Body | `/api/vitals` | Body composition summary, weight trend, lean mass charts |
| Settings | `/api/settings` | Sessions-per-week slider |
| Forms | Various POST endpoints | All 5 forms wired with toast feedback |

**Modified `templates/index.html`:**
- Added `<script src="/static/js/dashboard.js?v=20260413c">` after app.js
- Added `window.loadTabData(tabName)` call in inline `switchTab()` so data loads on tab navigation
- Added `window.navigateToTab = switchTab` alias
- Added no-cache meta tags
- Bumped CSS/JS cache busters to `?v=20260413c`

### Verification Steps (Post-Deploy)

1. Open dashboard URL in mobile Safari
2. Login
3. Dashboard tab: verify KPIs show numbers (not `--`), readiness gauge shows score, next workout shows exercises
4. Click Vitals tab: verify weight, HR, sleep, activity cards populated
5. Click Analytics tab: verify insights and consistency stats
6. Click Body tab: verify body composition summary
7. Click Log tab: submit a set, verify toast confirmation
8. Verify tab switching works on all 9 tabs

---

## Apple Health Auto-Sync Architecture

### Current State (Rejected as End-State)

The current Apple Health integration requires **manual JSON export**:
1. Open iPhone Health app
2. Tap profile → Export Health Data
3. Download ZIP
4. Extract JSON to `~/Documents/Health/` on the server
5. Dashboard reads files via `apple_health_parser.py`

**Why this fails:**
- User must remember to export (no reminder/trigger)
- No scheduling capability
- Manual file management on server
- Data is always stale until next manual export
- High friction for a daily-use gym app

### Apple Platform Constraints (Honest Assessment)

Apple restricts HealthKit data access:

1. **Web browsers cannot access HealthKit.** Safari has no HealthKit Web API. This is by design — Apple treats health data as highly sensitive.
2. **Background sync requires a native app.** Only iOS apps with HealthKit entitlement can read health data in background.
3. **App Store review applies.** Any HealthKit app must pass Apple's review, including justification for data access.
4. **No third-party auto-push.** There's no iOS service that automatically pushes HealthKit data to arbitrary webhooks.
5. **iOS Shortcuts can export but are unreliable for automation.** Shortcuts can schedule, but iOS kills background shortcuts unpredictably.

### Options Evaluated

#### Option A: Native iOS Companion App (LONG-TERM — Best Path)

**How it works:**
1. Build minimal iOS app with HealthKit capability
2. User grants HealthKit permissions once
3. App syncs data to `/api/apple-health/sync` endpoint on launch + periodically via background delivery
4. Dashboard shows live data with no manual action

**What the app does:**
- Read: workouts, steps, active energy, heart rate (RHR, HRV), sleep analysis, body measurements
- Sync: batch POST to backend with user auth token
- Background: use HKObserverQuery to trigger sync on new data

**Pros:**
- True automatic sync — zero user action after initial setup
- Apple-approved, privacy-respecting
- Can use background delivery for near-real-time sync
- Full access to all HealthKit data types

**Cons:**
- Requires iOS development (Swift/SwiftUI)
- App Store review (1-2 weeks)
- Apple Developer account ($99/year)
- Must maintain app across iOS versions
- User must install separate app

**Implementation Time:** 3-4 weeks for MVP
**Complexity:** High
**User Friction:** Low (after initial install)

**Architecture:**
```
[HealthKit on iPhone]
       ↓ (HKObserverQuery triggers)
[iOS Companion App]
       ↓ (authenticated HTTPS POST)
[/api/apple-health/sync]
       ↓ (parse + upsert)
[SQLite on HQ Mac Mini]
       ↓ (read)
[Dashboard :5050]
```

**API Design:**
```json
POST /api/apple-health/sync
Authorization: Bearer <token>
Content-Type: application/json

{
  "workouts": [...],
  "heart_rate": [...],
  "sleep": [...],
  "steps": [...],
  "active_energy": [...],
  "sync_token": "2026-04-13T18:00:00Z"
}
```

The `sync_token` enables incremental sync — only data newer than the token is sent.

#### Option B: iOS Shortcuts Automation (INTERIM — Deploy Now)

**How it works:**
1. Create an iOS Shortcut that exports HealthKit data
2. Schedule via Shortcuts Automation (time-based: 6am, 10pm)
3. Shortcut uploads to a simple file-receiver endpoint on the server
4. Server file watcher processes new data

**Pros:**
- No app development needed
- Can deploy in hours
- Uses built-in iOS tools

**Cons:**
- iOS kills background shortcuts unpredictably
- Not truly "set and forget"
- User must set up shortcut manually
- Limited data types (Shortcuts can export, but format varies)

**Implementation Time:** 1-2 days
**Complexity:** Low
**User Friction:** Medium (one-time setup, then unreliable)

#### Option C: Health Auto-Export App + Webhook (INTERIM — Better Than B)

**How it works:**
1. Use an existing App Store app like "Health Auto Export" ($4.99)
2. Configure it to POST to our `/api/apple-health/sync` endpoint
3. App handles HealthKit reading, scheduling, and retry

**Pros:**
- Existing app, no development needed
- More reliable than Shortcuts
- Supports auto-export scheduling
- Can POST JSON directly to webhook

**Cons:**
- Depends on third-party app continuing to work
- App may change pricing/features
- Less control over data format
- Still requires user to install and configure app

**Implementation Time:** 1 day (backend endpoint only)
**Complexity:** Low
**User Friction:** Medium (install app + configure webhook URL + auth)

### Recommendation

| Phase | Approach | Timeline | Effort | Friction |
|-------|----------|----------|--------|----------|
| **Interim** | Option C: Health Auto Export app + webhook | 1 day | Low | Medium |
| **Long-term** | Option A: Native iOS companion app | 3-4 weeks | High | Low |

**Why Option C for interim:** The "Health Auto Export" app already exists on the App Store, already has HealthKit permissions, already supports scheduled auto-export to webhooks, and costs $4.99. We just need to build the receiving endpoint `/api/apple-health/sync` and add basic auth. This gets Wesley automatic sync within a day while we build the proper native app.

**Why Option A for long-term:** A custom app gives us full control over data format, sync frequency, error handling, and background delivery. It eliminates dependency on a third-party app. It's the only path to truly zero-friction automatic sync.

### Implementation Plan: Interim (Option C)

**Step 1: Build `/api/apple-health/sync` endpoint (4 hours)**
```python
@app.route('/api/apple-health/sync', methods=['POST'])
def apple_health_sync():
    """Receive auto-exported HealthKit data from Health Auto Export app."""
    data = request.get_json(force=True)
    # Parse workouts, heart rate, sleep, steps
    # Upsert into SQLite with dedup
    # Return sync_token for incremental sync
    return jsonify({"status": "ok", "sync_token": now_iso})
```

**Step 2: Add auth for webhook (2 hours)**
- Generate a sync token per user (stored in settings)
- Health Auto Export sends token in header
- Endpoint validates token before accepting data

**Step 3: Configure Health Auto Export (30 min)**
- Install app on Wesley's iPhone
- Grant HealthKit permissions
- Set webhook URL: `https://admins-mac-mini.tail6c6490.ts.net:5050/api/apple-health/sync`
- Set auth header with sync token
- Schedule: every 6 hours

**Step 4: Merge into dashboard data pipeline (2 hours)**
- Modify vitals/dashboard endpoints to also read from synced Apple Health data
- Deduplicate against Oura data (prefer Apple Health for workouts, Oura for sleep)
- Update `apple_health_parser.py` to support both file-based and API-synced data

### Implementation Plan: Long-Term (Option A)

**Week 1: iOS App Skeleton**
- Create Xcode project with SwiftUI
- Add HealthKit capability and info.plist keys
- Implement permission request screen
- Create basic sync status UI

**Week 2: HealthKit Reading + Backend Endpoint**
- Implement HKObserverQuery for background triggers
- Read workouts, RHR, HRV, sleep, steps, active energy
- Build `/api/apple-health/sync` endpoint (shared with interim)
- Implement incremental sync with sync_token

**Week 3: Background Sync + Auth**
- Implement background delivery (HKObserverQuery)
- Add authentication (JWT or API key)
- Handle network errors and retry logic
- Test with real data

**Week 4: Polish + App Store**
- Add sync history UI
- Handle edge cases (revoked permissions, offline)
- Submit to App Store review
- Document user setup flow

**Risks:**
- App Store may reject if HealthKit justification isn't clear enough
- Background delivery may be throttled by iOS (document workarounds)
- User must keep app installed for sync to continue

**Privacy & Security:**
- All data transmitted over HTTPS (Tailscale Serve already handles this)
- No third-party data sharing
- HealthKit access can be revoked in iPhone Settings at any time
- Data stored on user's own hardware (Mac Mini), not cloud

---

## Summary

| Issue | Status | Fix |
|-------|--------|-----|
| Dashboard data not showing | **FIXED** | Created `dashboard.js` data layer |
| Forms not submitting | **FIXED** | Wired all forms to API endpoints |
| Tab data loading | **FIXED** | `loadTabData()` hook in switchTab |
| Stale cache | **FIXED** | Cache busters + no-store meta tags |
| Manual Apple Health export | **INTERIM PLANNED** | Option C (Health Auto Export + webhook) |
| True automatic Apple Health sync | **PLANNED** | Option A (native iOS app, 3-4 weeks) |