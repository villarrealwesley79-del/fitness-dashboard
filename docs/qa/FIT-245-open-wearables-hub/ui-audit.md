# FIT-246 Open Wearables Hub UI Audit

Date: 2026-06-26

Issue: FIT-246

Worktree: `/Users/admin/codex-worktrees/fitness-fit-246-open-wearables-hub-foundation`

Local URL: `http://localhost:5063`

Audit account: local throwaway user `fit245audit`

Open Wearables proof source: localhost-only fake bridge on `http://localhost:8018` with two provider rows and normalized sleep/activity fixtures.

## Summary

PASS with environment caveats. The Open Wearables hub row renders in Settings, provider probing shows connected hub providers, sync imports normalized facts, recommendation source proof uses Open Wearables only as a bounded conservative modifier after facts exist, AI fact query exposes approve/reject controls without mutation, History and Stats empty states render cleanly, and mobile Settings does not overlap the bottom nav.

## Evidence

- Settings desktop: `output/playwright/FIT-246/settings-desktop.png`
- Settings mobile: `output/playwright/FIT-246/settings-mobile.png`
- History desktop empty state: `output/playwright/FIT-246/history-desktop-empty.png`
- Stats desktop empty state: `output/playwright/FIT-246/stats-desktop-empty.png`
- Settings connected desktop: `output/playwright/FIT-246/settings-connected-desktop.png`
- Settings connected mobile: `output/playwright/FIT-246/settings-connected-mobile.png`
- Dashboard bounded modifier desktop: `output/playwright/FIT-246/dashboard-bounded-modifier-desktop.png`
- Stats connected desktop: `output/playwright/FIT-246/stats-desktop-connected.png`

## States Checked

- Settings hub missing config: PASS. Shows `Setup needed`, redacted base URL, disabled Sync, `No hub providers visible yet`, and normalized-facts policy.
- Settings setup/check action: PASS. `POST /api/open-wearables/setup/check` returned 200 and the UI showed `Open Wearables checked.`
- Settings manual sync blocked state: PASS. Sync is disabled while hub config is missing.
- Settings connected provider state: PASS. With the local fake hub configured, `/api/open-wearables/status` probed providers and Settings showed `2 hub providers visible`.
- Settings provider rows: PASS. Existing WHOOP, Oura, and Apple Health rows remain visible under the hub.
- Open Wearables sync: PASS. Manual sync imported normalized facts only: `steps`, `active_minutes`, `resting_heart_rate`, `sleep_avg_heart_rate`, and `sleep_duration`.
- Recommendation source proof: PASS. Before facts, Dashboard source proof includes `Open Wearables for display only`; after sync, it shows `Open Wearables for bounded modifier` and conservative reasoning from sleep/activity facts.
- AI fact query: PASS. Querying `strength history?` returned a sanitized no-evidence answer, exposed Approve/Reject controls, and Approve returned `No records were changed in this version.`
- History empty state: PASS. Frequency, volume, top exercises, and recent workouts empty states render without crashing.
- Stats empty state: PASS. KPI totals, muscle recovery, donut empty state, and insights empty state render without overlap.
- Mobile Settings: PASS. Open Wearables row, AI fact query row, warning states, and bottom nav fit at 390 x 844.
- Apple Health workout fallback: PASS after fix. `/api/apple-health/workouts?days=30` now returns 200 with an empty list when no data exists, preventing browser-level 404 noise in History.
- Console review: PASS with local-environment caveat. Browser console errors were Oura unavailable responses only; no Open Wearables, AI suggestion, History, or Stats runtime errors appeared.

## Caveats

- Live provider-connected state was audited with a localhost fake Open Wearables bridge, not real provider credentials.
- Oura live sync returned expected local-environment 503/500 responses because Oura is not configured in this worktree.
- The first unauthenticated `/api/ai/health` request returned 401 before auth scope initialized, then subsequent authenticated health checks returned 200.
- The app currently presents a dark visual theme; no separate light theme toggle was available to exercise as a true light-mode state.

## Fixes From Audit

- Replaced an out-of-scope `ago()` call in the Open Wearables Settings painter with `fmtDateTime()`.
- Changed Open Wearables provider count wording so fallback rows are not counted as hub providers.
- Changed the Apple Health workouts no-data response from 404 to `200 {workouts: [], total: 0}` for History.
- Changed `/api/open-wearables/status` so configured hubs include provider probe results, not only config-shape status.
- Added same-day Strength Training merge policy so logged lifts and Watch strength sessions share one canonical count with provenance.
- Added visible AI suggestion Approve/Reject controls; approval remains non-mutating in this slice.

## Commands

```bash
python3 -m pytest tests/test_open_wearables_adapter.py tests/test_open_wearables_health_sync_redaction.py tests/test_wearable_fact_store.py tests/test_history_canonical_categories.py tests/test_recommendation_sources.py tests/test_ai_fact_context.py tests/test_open_wearables_ui_contract.py tests/test_wearable_freshness_contract.py tests/test_history_detail_and_analyze.py tests/test_dashboard_render_contract.py tests/test_apple_health_recommendation_bridge.py tests/test_whoop_recommendations.py tests/test_progress_loop_completion_to_recommendation.py tests/test_whoop_source_conflicts.py -q
python3 -m py_compile app.py open_wearables_adapter.py wearable_fact_store.py history_normalization.py recommendation_sources.py ai_fact_query.py
node --check static/js/app.js
```
