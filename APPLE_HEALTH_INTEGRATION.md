# Apple Health Integration — Build Output

**Ticket:** 63547188-44d8-4100-97dc-9bd775de2264
**Worker:** Charlie
**Completed:** 2026-04-13

## Integration Path
**JSON Export Ingestion** — Wesley has HealthKit JSON exports at `~/Documents/Health/` (398 workouts, 537 sleep days, 731 step days, 583 RHR, 646 HRV). Zero auth, zero new infra.

## Files Created/Modified

### NEW: `apple_health_parser.py` (9,229 bytes)
- Parses HealthKit JSON exports from `~/Documents/Health/`
- 6 API endpoints via `register_apple_health_routes(flask_app)`:
  - `GET /api/apple-health/summary`
  - `GET /api/apple-health/workouts?days=N`
  - `GET /api/apple-health/sleep?days=N`
  - `GET /api/apple-health/steps?days=N`
  - `GET /api/apple-health/vitals?days=N`
  - `GET /api/apple-health/status`

### MODIFIED: `app.py`
- Line 49: `from apple_health_parser import register_apple_health_routes`
- Line 58: `register_apple_health_routes(app)` after Stripe blueprint

### MODIFIED: `templates/index.html`
- Added 🍎 Health tab button in bottom nav
- Added `<section id="apple-health">` with status card, summary cards, workouts table, sleep/steps charts, vitals table
- Added tab click delegation JS (fixes existing bug where `data-tab` buttons had no click handlers)
- Added `loadAppleHealth()` JS that fetches all 5 API endpoints

## Setup
1. Export Apple Health data from iPhone: Health app → profile → Export All Health Data
2. Copy `healthkit_*.json` files to `~/Documents/Health/`
3. Restart Flask app — Health tab auto-populates on click

## Limitations
- Requires manual JSON export from iPhone (no live sync)
- Large exports handled in-memory; very large files may need streaming
- Deduplication is timestamp-based; re-uploading same export is safe