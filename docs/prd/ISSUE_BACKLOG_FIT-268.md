# FIT-268 Issue Backlog — RESOLVED: all issues filed on 2026-07-09

> **Status update (2026-07-09):** Workspace capacity was freed (95 old Done issues in the Personal team deleted, recoverable from Linear trash for 30 days). All 74 issues are now filed: FIT-269..FIT-276 (first batch), FIT-277 (owner-guard fail-open), and the 65 below. This document is retained as the audit trail of the payloads; Linear is the source of truth going forward.

| ID | Title |
|----|-------|
| FIT-278 | Verify Open Food Facts attribution and rate limits |
| FIT-279 | Validate body measurement date and tape fields |
| FIT-280 | Verify Nutritionix quota, cache TTL, and redistribution |
| FIT-281 | Define full local-data deletion boundaries |
| FIT-282 | Pin Oura trends and sleep-summary contracts with tests |
| FIT-283 | Surface provider fallback details in the review card |
| FIT-284 | Productize the Navy calculator and tape-measure flow |
| FIT-285 | Delete push subscriptions during account data deletion |
| FIT-286 | Add cross-source ordering tests for food, WHOOP, and Open Wearables |
| FIT-287 | Refresh stale Oura cache without requiring Settings |
| FIT-288 | Add a machine-readable runtime artifact policy check |
| FIT-289 | Remove raw provider payload from weather response |
| FIT-290 | Add TTL or invalidation policy for food lookup caches |
| FIT-291 | Add a non-mutating smoke mode for partially degraded local runtimes |
| FIT-292 | Return CSV import row outcomes instead of only total upserts |
| FIT-293 | Fix or remove the dashboard RPE chip contract |
| FIT-294 | Make backup import transactional or explicitly resumable |
| FIT-295 | Make Pro entitlement gates honest and explicit |
| FIT-296 | Add an operator status command for launchd and smoke readiness |
| FIT-297 | Add a real `/landing` route or remove the public page |
| FIT-298 | Add visible warning when active workout draft cannot persist |
| FIT-299 | Register or remove the Stripe blueprint contract |
| FIT-300 | Separate metadata check naming from durable fact sync |
| FIT-301 | Document or fix Plank target weight clamping |
| FIT-302 | Add offline dashboard state QA beyond service-worker fallback |
| FIT-303 | Wire scheduled reminder delivery or label preview-only in UI |
| FIT-304 | Add end-to-end browser QA for push setup and click behavior |
| FIT-305 | Add a vision health/preflight status endpoint for Settings and Log |
| FIT-306 | Add browser QA for active workout modal states |
| FIT-307 | Make configured session lifetime effective or remove the inert config |
| FIT-308 | Add explicit tests for owner-only non-owner denial |
| FIT-309 | Add staleness watchdog unit coverage with temporary SQLite DBs |
| FIT-310 | Add focused tests for body and sleep endpoints |
| FIT-311 | Add shellcheck/static validation for ops shell scripts |
| FIT-312 | Redact raw send exception details from push test responses |
| FIT-313 | Remove unsourced public metrics and testimonials or back them |
| FIT-314 | Add credentialed provider smoke mode with safe output |
| FIT-315 | Fix deload detector key mismatch so deload recommendation can fire |
| FIT-316 | Add subscription retention and cleanup policy |
| FIT-317 | Validate stored permission_state values |
| FIT-318 | Wire or remove orphaned clear_food_logs/delete_user_data helpers |
| FIT-319 | Add webhook idempotency and event audit trail |
| FIT-320 | Add live mobile QA for multi-photo and offline replay states |
| FIT-321 | Fix inert Oura HRV trend input in advanced analytics |
| FIT-322 | Align progress insight type names with frontend styling |
| FIT-323 | Replace history delete-by-index with stable ids |
| FIT-324 | Align pricing copy with the single-owner app model |
| FIT-325 | Resolve JSON settings vs SQL settings default drift |
| FIT-326 | Add body-mass ingestion or remove it from helper SLA scope |
| FIT-327 | Make progressive overload either visible or explicitly API-only |
| FIT-328 | Add visible retry scheduling for offline meal sync |
| FIT-329 | Add contract tests for advanced analytics thresholds |
| FIT-330 | Clarify accepted/manual food-log filtering in data-store APIs |
| FIT-331 | Add payload limits and rejection reporting for HAE sync |
| FIT-332 | Add live sidecar smoke coverage outside unit fixtures |
| FIT-333 | Reconcile forced pending review with the auto-log contract |
| FIT-334 | Replace or label the H-E-B curated reference |
| FIT-335 | Align body-fat-only saves with API validation |
| FIT-336 | Clarify tomorrow-date tolerance in CSV imports |
| FIT-337 | Fix bedtime consistency for midnight wraparound |
| FIT-338 | Redact upstream Oura error detail before UI display |
| FIT-339 | Rename or clarify `/api/health/sync` ownership |
| FIT-340 | Harden Markdown export against partial workout rows |
| FIT-341 | Preserve manual sleep visibility when Oura rows exist |
| FIT-342 | Surface staleness watchdog evidence in Settings |

---

These verified, PRD-derived issues could not be filed on 2026-07-08 because the Linear workspace hit its free-plan issue limit mid-batch. They are priority-ordered and each maps 1:1 to a `save_issue` call (team `Fitness app`, state `Backlog`, related to FIT-268) once capacity is freed.

Already created before the quota tripped: FIT-269, FIT-270, FIT-271, FIT-272, FIT-273, FIT-274, FIT-275, FIT-276.

---
## 1. Verify Nutritionix quota, cache TTL, and redistribution

- **Priority:** High  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The Nutritionix client is implemented, but repository docs still mark live account quota, ToS cache duration, redistribution, and some endpoint details as unverified because the docs were blocked during research.

**Where**
`docs/nutrition_sources.md`; `nutritionix_client.py`; `branded_food_lookup.py`

**Why this matters**
A 180-day cache or committed/offline snapshot could violate provider terms if the assumptions are wrong.

**Acceptance criteria**
- Owner verifies current Nutritionix dashboard quota and ToS using the production account.
  - Cache TTL and offline/snapshot permissions are documented with date and source.
  - Code TTL is adjusted if the verified limit is shorter than 180 days.
  - PR/test docs state expected behavior when quota is exhausted.

**Source**
PRD [06-nutrition-data-sources.md § IC-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/06-nutrition-data-sources.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 2. Delete push subscriptions during account data deletion

- **Priority:** High  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The former test-only bulk deletion helper was updated by FIT-285 to delete active and revoked `push_subscriptions`. FIT-318 subsequently removed the still-unwired helper instead of exposing an unconfirmed destructive product flow.

**Where**
data_store.py:1955

**Why this matters**
A user data deletion flow can leave notification endpoints and browser subscription material behind.

**Acceptance criteria**
- Retired by FIT-318: there is no bulk account-data deletion helper or product flow.
- A future owner-confirmed deletion feature must cover active and revoked push subscriptions and every other user-scoped store.

**Source**
PRD [14-data-layer-persistence.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 3. Fix owner-guard fail-open when FITNESS_DASHBOARD_OWNER_USER_ID is invalid

- **Priority:** High  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
_owner_user_id() returns None on ValueError for a non-integer env value and never falls back to the MIN(id) query; _is_owner_user_id() treats owner_id None as "allow everyone". A typo in FITNESS_DASHBOARD_OWNER_USER_ID silently disables the owner guard for all authenticated users.

**Where**
auth.py:238-254

**Why this matters**
Security fail-open: a config typo removes owner-only protection on sensitive routes.

**Acceptance criteria**
- Invalid value either falls back to minimum users.id or fails closed with a clear boot error
- Unit test covers non-integer env value
- Behavior documented in docs/prd/01-auth-and-account.md

**Source**
PRD [01-auth-and-account.md § V-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/01-auth-and-account.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 4. Make Pro entitlement gates honest and explicit

- **Priority:** High  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Billing writes `users.is_pro`, but no inspected feature route or UI condition reads it to enforce Free vs Pro differences. Pricing and landing claim Pro controls Oura, recommendations, unlimited history, SaaS multi-user access, and support.

**Where**
`auth.py:134-141`, `stripe_checkout.py:112-121`, app feature routes

**Why this matters**
The product is either overpromising paid value or missing enforcement.

**Acceptance criteria**
- Decide whether subscription is currently decorative, informational, or enforced.
  - If enforced, list exact gated features and add route/UI checks.
  - If not enforced, revise copy and docs to say billing is experimental/dormant.

**Source**
PRD [13-billing-stripe-landing.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 5. Register or remove the Stripe blueprint contract

- **Priority:** High  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The repo defines Stripe routes in `stripe_checkout.py`, and auth publicly allowlists those paths, but current app wiring does not import or register `stripe_bp`. The route inventory calls them additional blueprint routes, yet the inspected app object will not serve them unless an external wrapper registers the blueprint.

**Where**
`stripe_checkout.py:11`, `app.py:168-179`

**Why this matters**
Billing pages and webhook behavior can appear implemented while being unreachable.

**Acceptance criteria**
- The app either registers `stripe_bp` intentionally or deletes/marks the blueprint as dormant.
  - Route tests cover `/pricing`, `/success`, `/cancel`, and `/webhook`.
  - Auth public allowlist matches the chosen route set.

**Source**
PRD [13-billing-stripe-landing.md § IC-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 6. Wire scheduled reminder delivery or label preview-only in UI

- **Priority:** High  |  **Labels:** Feature  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The app previews stale-data and pending-food reminder alerts, and can send a manual test push, but no scheduled job or trigger sends those reminder alerts as Web Push. UI copy says "no scheduled reminders yet" only in the subscribed detail/test payload, while the card is titled Coaching reminders.

**Where**
`app.py:13891`, `app.py:14090`, `static/js/app.js:6069`

**Why this matters**
Users may believe reminders are active after subscribing when only test delivery is wired.

**Acceptance criteria**
- Either implement a scheduler/on-demand send path for preview alerts, or rename/copy the UI as preview/test-only.
  - If implemented, sends must be idempotent per alert window and non-safety-critical.
  - Tests prove no sends occur without active subscription and VAPID private key.

**Source**
PRD [12-push-notifications.md § IC-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/12-push-notifications.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 7. Add focused tests for body and sleep endpoints

- **Priority:** High  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
No focused tests were found for body measurement logging/history/recomp, Navy calculator, manual sleep import, or sleep analytics. These endpoints handle personal progress data and contain several validation/precedence rules.

**Where**
`tests/`, `app.py:8549`, `app.py:15778`

**Why this matters**
Regressions in measurement and sleep data can silently corrupt trend interpretation.

**Acceptance criteria**
- Tests cover body add validation, sorting, trend labels, rolling averages, and ETA edge cases.
  - Tests cover Navy male/female formula validation and output clamping.
  - Tests cover sleep import JSON/CSV, replacement by date, invalid rows, and Oura/manual precedence.
  - Tests run without private runtime data.

**Source**
PRD [16-progress-analytics-body.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 8. Fix deload detector key mismatch so deload recommendation can fire

- **Priority:** High  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
The caller reads detect_deload_need(...).get('recommended', False) but the detector returns the key 'needed'. The deload_recommended branch can never fire from the detector path.

**Where**
app.py:15918, app.py:4131-4137

**Why this matters**
A safety feature (deload recommendation) is silently dead in analytics output.

**Acceptance criteria**
- Caller and detector agree on one key
- Unit test asserts deload_recommended True when detector conditions met

**Source**
PRD [16-progress-analytics-body.md § V-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 9. Fix inert Oura HRV trend input in advanced analytics

- **Priority:** High  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
compute_hrv_trend(OURA_DB_FILE) passes a DB path, but the function expects a list of HRV values and returns a trend string. The HRV penalty logic in advanced analytics therefore always evaluates the unknown/default branch and the documented up/stable/down penalties never apply.

**Where**
app.py:15898, oura_client.py:412

**Why this matters**
A documented recovery signal silently contributes nothing; analytics claims precision it does not have.

**Acceptance criteria**
- Advanced analytics fetches HRV values and passes them to compute_hrv_trend
- Penalty branches (up/stable/down) covered by unit tests
- Verified via /api/analytics/advanced response change

**Source**
PRD [16-progress-analytics-body.md § V-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 10. Add body-mass ingestion or remove it from helper SLA scope

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
The helper SLA says a future native helper may read body-mass data already used by the dashboard, but the current HAE parser does not map body mass/weight metrics.

**Where**
`docs/APPLE_HEALTH_HELPER_SLA.md:21`, `apple_health_parser.py:631`

**Why this matters**
The documented bridge scope overstates the current Apple Health data contract.

**Acceptance criteria**
- Decide whether Apple Health body mass belongs in the HAE bridge.
  - If yes, add metric mapping, persistence, UI/status evidence, and tests.
  - If no, update SLA wording to avoid claiming body-mass parity.

**Source**
PRD [08-apple-health-integration.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/08-apple-health-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 11. Add payload limits and rejection reporting for HAE sync

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
`/api/apple-health/sync` accepts arbitrary JSON size and counts per-record parse failures as skipped without reporting why.

**Where**
`apple_health_parser.py:774`, `apple_health_parser.py:815`

**Why this matters**
A bad automation or replay can create large local writes and leave the owner guessing why records did not land.

**Acceptance criteria**
- Define maximum request size and maximum records per sync attempt.
  - Return stable public rejection codes/counts by reason.
  - Store attempt summary without raw health payload leakage.
  - Add tests for over-limit payload and invalid rows.

**Source**
PRD [08-apple-health-integration.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/08-apple-health-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 12. Pin Oura trends and sleep-summary contracts with tests

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
Tests pin Oura status and sync-sleep, but they do not pin trend fallback behavior, raw-json stripping, bedtime consistency thresholds, or daily-vs-sleep fallback behavior.

**Where**
`app.py:13376`, `app.py:13496`, `oura_sleep_sync.py:235`

**Why this matters**
These endpoints feed dashboard confidence and sleep cards; regressions would be user-visible.

**Acceptance criteria**
- Add tests for `/api/oura/trends` with enough cache rows and with API fallback failure.
  - Add tests for `/api/oura/sleep-summary` daily-cache augmentation.
  - Assert `raw_json` is not returned in public trend series.
  - Assert consistency status thresholds.

**Source**
PRD [07-oura-integration.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/07-oura-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 13. Refresh stale Oura cache without requiring Settings

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
Dashboard Oura status uses today's cached row unless the caller passes `refresh=true`; a stale `created_at` on today's row can remain cached until the user opens Settings or the date changes.

**Where**
`app.py:13227`, `static/js/app.js:1156`, `static/js/app.js:5355`

**Why this matters**
The first-screen brief can look current while relying on an old same-day cache.

**Acceptance criteria**
- Define a server-side refresh TTL for today's Oura row.
  - Auto-refresh only when the row is older than that TTL and a token exists.
  - Preserve cached fallback behavior when Oura is unavailable.
  - Add tests for fresh cache, stale cache refresh, and stale cache fallback.

**Source**
PRD [07-oura-integration.md § IC-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/07-oura-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 14. Return CSV import row outcomes instead of only total upserts

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
Unsupported CSV record types are skipped, and the response reports only imported/upserted records. The owner cannot tell whether rows were ignored because they were unsupported, naps, duplicates, or malformed-but-skipped before normalization.

**Where**
app.py:/api/whoop/import-csv

**Why this matters**
A user can believe a WHOOP export was fully imported when meaningful rows were silently omitted.

**Acceptance criteria**
- Response includes counts for parsed rows, imported rows, skipped unsupported rows, ignored naps, and duplicates/upserts.
  - UI shows a concise import summary after success.
  - Existing validation failures for invalid metrics, UTF-8, row cap, and future dates remain hard failures.
  - Add tests for mixed supported/unsupported CSV imports.

**Source**
PRD [09-whoop-integration.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/09-whoop-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 15. Separate metadata check naming from durable fact sync

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
`/api/health/sync` sounds like a durable sync but returns metadata counts only and does not store facts. `/api/open-wearables/sync` performs the durable normalized fact write. The behavior is safe, but the route naming can mislead future callers.

**Where**
/api/health/sync; /api/open-wearables/sync

**Why this matters**
A caller may use `/api/health/sync` and assume recommendation facts were refreshed when they were not.

**Acceptance criteria**
- Document `/api/health/sync` as metadata-only in route docs and UI-facing developer docs.
  - Consider aliasing a clearer route name such as `/api/open-wearables/check-sync` while preserving compatibility.
  - Add a contract test that `/api/health/sync` never writes facts.
  - Ensure UI sync buttons call the durable Open Wearables route.

**Source**
PRD [10-open-wearables-integration.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/10-open-wearables-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 16. Add a vision health/preflight status endpoint for Settings and Log

- **Priority:** Medium  |  **Labels:** Feature  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
Settings shows general AI coach health, but the meal composer has no vision-specific readiness indicator. A user only discovers missing VLM config, unloaded model, or fallback routing after submitting a meal.

**Where**
`vision_estimator.py`, `local_vision_adapter.py:285`, `templates/index.html:157`

**Why this matters**
Photo logging is a mobile capture workflow; preflight clarity prevents wasted uploads and confusing failures.

**Acceptance criteria**
- Authenticated endpoint returns configured provider, candidate roles, reachable/model-loaded status, and sanitized errors.
  - Meal composer shows "ready", "warming", "fallback", or "unavailable" without exposing secrets or URLs unless intended.
  - Status does not send real food images and does not mutate meal state.

**Source**
PRD [05-photo-food-logging-vision.md § IC-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/05-photo-food-logging-vision.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 17. Add credentialed provider smoke mode with safe output

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The smoke helper is read-only and useful, but the committed FIT-98 result was environment-limited because Nutritionix and USDA credentials were missing. There is no standardized credentialed smoke report shape that proves live regional/provider coverage without leaking secrets or mutating caches.

**Where**
`scripts/smoke_branded_lookup_coverage.py`; `docs/nutrition_sources.md`

**Why this matters**
Provider coverage can appear broken in clean CI while working locally, or vice versa, without a safe evidence artifact.

**Acceptance criteria**
- Add a documented credentialed smoke command that redacts secrets and disables cache writes.
  - Output separates provider unavailable, no match, wrong-chain match, and accepted match.
  - Report includes provider status, source priority, cache mode, and direct-gate mode.
  - Tests cover report formatting and redaction without live network.

**Source**
PRD [06-nutrition-data-sources.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/06-nutrition-data-sources.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 18. Add live mobile QA for multi-photo and offline replay states

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The code implements multi-photo capture, thumbnail removal, offline IndexedDB storage, auth-scope gating, and retry cleanup, but this PRD pass did not find live mobile QA proof for those interactive states in the assigned evidence.

**Where**
`static/js/app.js:9577`, `static/js/app.js:7824`, `templates/index.html:157`

**Why this matters**
Photo food logging is primarily mobile; broken file-picker, preview, or offline cleanup behavior can silently create trust and privacy failures.

**Acceptance criteria**
- QA covers iOS/Android or equivalent mobile browser capture/upload behavior.
  - QA covers four-photo cap, over-size errors, thumbnail removal, offline queue, reconnect replay, and discard cleanup.
  - Proof confirms raw photo blobs are removed after sync/discard.

**Source**
PRD [05-photo-food-logging-vision.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/05-photo-food-logging-vision.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: PLAUSIBLE by independent code review.

**Verifier note:** All cited code exists: offline queue constants at app.js:7827-7831 (fitMealIntakeQueueDB/queued_meals/meal_photos, cite 7824 in range), composer draft/photo state at app.js:9580-9584 (MEAL_DRAFT_KEY, MEAL_MAX_PHOTOS=4, cite 9577 in range), composer markup at templates/index.html:156-172 (cite 157 exact). The absence of live mobile QA evidence is consistent with the repo (no QA artifacts found for these flows) but a negative claim about external evidence cannot be fully confirmed from code alone.

---
## 19. Add visible retry scheduling for offline meal sync

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
Offline meal sync retries serially on boot/reconnect and manual retry, but the queue does not expose a next retry time, backoff policy, or maximum retry behavior. Failed entries can remain as `pending` or `auth_required` without a clear schedule.

**Where**
`static/js/app.js` offline meal queue; sync queue modal

**Why this matters**
The owner needs to know whether a saved meal is actively retrying, waiting for auth, or stuck.

**Acceptance criteria**
- Queue rows show last attempt time, next automatic retry condition, and attempt count.
  - Network/server failures use a documented backoff or explicit reconnect-only policy.
  - Auth-required entries explain the account mismatch and do not retry until scope is refreshed.
  - Tests cover pending, auth-required, rejected, conflicted, and eviction-failed display states.

**Source**
PRD [04-meal-logging-text-barcode.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/04-meal-logging-text-barcode.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 20. Reconcile forced pending review with the auto-log contract

- **Priority:** Medium  |  **Labels:** none  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The public contract still describes `logged` as a normal immediate success state when confidence is high. The current route forces all fresh submissions into `pending_review`, making the policy engine advisory for this endpoint.

**Where**
`docs/MEAL_INTAKE_CONTRACT.md`; `app.py` `POST /api/meal-intake`; `meal_log_policy.py`

**Why this matters**
Future agents may rebuild auto-log behavior from the stale contract and accidentally start counting uncertain meals without review.

**Acceptance criteria**
- Contract states whether auto-log is currently disabled, feature-flagged, or planned.
  - Tests assert the intended status for high-confidence text/barcode estimates.
  - UI copy matches the server's review-first behavior.
  - Policy module docs distinguish theoretical policy from route behavior.

**Source**
PRD [04-meal-logging-text-barcode.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/04-meal-logging-text-barcode.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 21. Replace or label the H-E-B curated reference

- **Priority:** Medium  |  **Labels:** none  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The H-E-B path is not a general H-E-B API client; it is a hardcoded curated estimate for one product URL. That is useful, but it can be mistaken for a real live H-E-B provider integration.

**Where**
`heb_product_lookup.py`; `branded_food_lookup.py`; source labels

**Why this matters**
Product and agents may overestimate H-E-B coverage and file fewer lookup gaps than the owner actually experiences.

**Acceptance criteria**
- Source label or docs call this a curated H-E-B reference, not a live H-E-B provider.
  - Tests keep variant/quantity rejection strict.
  - New H-E-B products require either curated entries with source evidence or a real provider plan.
  - Smoke reports list curated references separately from live provider results.

**Source**
PRD [06-nutrition-data-sources.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/06-nutrition-data-sources.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 22. Verify Open Food Facts attribution and rate limits

- **Priority:** Medium  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
The app carries best-effort OFF attribution text and a User-Agent, but the exact current attribution format, share-alike implications, and rate-limit policy remain partially unverified in the docs.

**Where**
`docs/nutrition_sources.md`; `open_food_facts_client.py`; `static/js/app.js` provenance rendering

**Why this matters**
OFF data has license obligations; incorrect or missing attribution creates compliance risk even in a local-first app.

**Acceptance criteria**
- Verify current OFF API docs and terms for attribution, User-Agent, and rate limits.
  - Update docs and UI attribution text if required.
  - Tests assert OFF attribution reaches review/detail surfaces after cache replay.
  - Rate-limit/backoff behavior is documented or implemented if OFF requires it.

**Source**
PRD [06-nutrition-data-sources.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/06-nutrition-data-sources.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 23. Add a machine-readable runtime artifact policy check

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
`.dockerignore` has tests, but repo hygiene rules also cover Git/source control and local cleanup behavior. There is no single machine-readable policy tying ignored runtime classes to docs and tests.

**Where**
`docs/REPO_HYGIENE.md:18-35`, `.dockerignore:1-54`

**Why this matters**
New artifacts can drift into unclear status and be committed or deleted accidentally.

**Acceptance criteria**
- Add a small policy manifest or test fixture listing sensitive/runtime artifact classes.
  - Assert `.dockerignore`, `.gitignore` [if present], and docs cover each class.
  - Include WHOOP, Apple Health, auth DB, AI cache, backup bundles, audit bundles, and JSON stores.

**Source**
PRD [15-ops-deployment.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/15-ops-deployment.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 24. Add a non-mutating smoke mode for partially degraded local runtimes

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The smoke test requires `/api/ai/health` to have reachable loaded model routes and sends a safe rejected workout POST. That is useful for release proof, but it can be too strict for diagnosing auth, launchd, or route health when AI hardware is intentionally offline.

**Where**
`support/self_test.sh:164-211`

**Why this matters**
Operators need a lighter smoke to distinguish "app is down" from "AI host is degraded."

**Acceptance criteria**
- Add an explicit mode such as route-only or degraded-ok without changing the default strict release smoke.
  - Document which checks are skipped or downgraded.
  - Tests cover argument parsing and no-secret output behavior.

**Source**
PRD [15-ops-deployment.md § IC-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/15-ops-deployment.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 25. Add a real `/landing` route or remove the public page

- **Priority:** Medium  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
A complete landing template exists and `/landing` is public, but no route renders the template in the current checkout.

**Where**
`templates/landing.html`, `auth.py:344`

**Why this matters**
The public acquisition surface cannot be relied on by operators or tests.

**Acceptance criteria**
- `/landing` renders the template or the template/public allowlist entry is removed.
  - Tests cover the chosen route behavior.
  - Navigation between landing, login, register, and pricing is verified.

**Source**
PRD [13-billing-stripe-landing.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 26. Add end-to-end browser QA for push setup and click behavior

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Static tests assert the relevant code exists, but no assigned evidence proves browser permission, service-worker subscription, visible notification, and notification-click focus behavior end to end.

**Where**
`static/js/app.js:6335`, `static/js/sw.js:29`

**Why this matters**
Push is browser/platform-sensitive, especially installed iOS PWA behavior.

**Acceptance criteria**
- Browser QA covers unsupported, denied, iOS not-installed, granted inactive, granted active, and test delivered states.
  - Service-worker notification click is verified to focus/open the expected URL.
  - QA notes platform/browser limitations honestly.

**Source**
PRD [12-push-notifications.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/12-push-notifications.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 27. Add staleness watchdog unit coverage with temporary SQLite DBs

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The watchdog contract is important, but current assigned tests do not directly exercise missing DB, no rows, fresh rows, stale rows, malformed timestamps, and query errors through the shell script.

**Where**
`scripts/check-apple-health-staleness.sh:21-80`

**Why this matters**
Apple Health freshness is an operator-facing trust signal.

**Acceptance criteria**
- Tests create temp SQLite DBs with `ah_sync_events` and `ah_sync_log` cases.
  - Tests assert exit codes and log messages for quiet, OK, stale, parse, and query-error paths.
  - Tests cover `STALE_AFTER_HOURS` override.

**Source**
PRD [15-ops-deployment.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/15-ops-deployment.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 28. Add subscription retention and cleanup policy

- **Priority:** Medium  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Revoked subscriptions remain in SQLite indefinitely unless manually cleaned by future code. The active list hides revoked rows, but raw endpoint and key material remain stored.

**Where**
`data_store.py:461`, `data_store.py:198`, `data_store.py:227`

**Why this matters**
Push subscription endpoints and auth keys are sensitive delivery material and should not persist forever after revocation.

**Acceptance criteria**
- Define retention window for revoked subscriptions.
  - Add cleanup routine or migration-safe pruning path.
  - Ensure active subscriptions are not accidentally deleted.
  - Add tests for revoked-row pruning and active-row preservation.

**Source**
PRD [12-push-notifications.md § IC-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/12-push-notifications.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 29. Add webhook idempotency and event audit trail

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Webhook processing does not store Stripe event IDs, event timestamps, or processing status. Duplicate delivery is mostly harmless for current mark/revoke writes, but there is no audit trail or replay protection.

**Where**
`stripe_checkout.py:91-109`, `auth.py:84-112`

**Why this matters**
Billing support needs to explain why an entitlement changed and safely handle repeated Stripe delivery.

**Acceptance criteria**
- Persist processed Stripe event IDs and status in a local table under `DATA_DIR`.
  - Duplicate events return 200 without reapplying side effects.
  - Operator docs describe where to inspect billing event history.

**Source**
PRD [13-billing-stripe-landing.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 30. Align pricing copy with the single-owner app model

- **Priority:** Medium  |  **Labels:** none  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Pricing promises SaaS multi-user access and trial expiration behavior, while current docs and data model describe a single-owner local-first app with no trial-expiration store.

**Where**
`templates/pricing.html:90-92`, `templates/landing.html:739-743`

**Why this matters**
The billing surface sets expectations the current app cannot satisfy.

**Acceptance criteria**
- Pricing copy matches actual local-first capabilities.
  - If trial expiration is desired, add explicit trial state and enforcement.
  - If multi-user is desired, create a separate data isolation/security spec before marketing it.

**Source**
PRD [13-billing-stripe-landing.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 31. Clarify accepted/manual food-log filtering in data-store APIs

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
`get_food_logs` returns rows ordered by logged time without filtering by accepted/correction state, while recommendation/adaptation logic separately excludes pending/review states. The data-store API name/commenting can mislead callers into treating all returned rows as accepted.

**Where**
data_store.py:685; data_store.py:1810

**Why this matters**
A future feature could accidentally count pending food estimates in nutrition totals or plan changes.

**Acceptance criteria**
- Document or rename the all-rows food-log helper.
  - Add an explicit accepted-food-log query helper.
  - Tests cover pending, manual, accepted, and review states for both helpers.

**Source**
PRD [14-data-layer-persistence.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 32. Define full local-data deletion boundaries

- **Priority:** Medium  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Account deletion only deletes selected `fitness_data.db` tables. It does not remove JSON history, auth rows, Oura/Apple Health/WHOOP/wearable fact DBs, protected token material, Open Wearables config, AI cache, or sync lock files.

**Where**
data_store.py:1955; app.py runtime store declarations at app.py:199

**Why this matters**
Product language around deleting user data could overpromise unless the boundary is explicit.

**Acceptance criteria**
- Product copy and API docs distinguish structured food/body data deletion from full local purge.
  - Add a separate full local purge flow or document manual deletion steps.
  - Tests or dry-run output list every store affected and not affected.

**Source**
PRD [14-data-layer-persistence.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 33. Make backup import transactional or explicitly resumable

- **Priority:** Medium  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Backup import restores JSON stores, then imports food/vocab/meal SQLite rows, then WHOOP facts. These operations are not one cross-store transaction, so a later failure can leave a partially restored runtime.

**Where**
app.py:15243

**Why this matters**
A failed restore can mix old and new owner data without a clear recovery path.

**Acceptance criteria**
- Import either stages and swaps all stores atomically where feasible or writes a resumable import journal.
  - Failure response identifies which stores were mutated.
  - Tests inject a failure after JSON restore and prove rollback or documented resumability.

**Source**
PRD [14-data-layer-persistence.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 34. Make configured session lifetime effective or remove the inert config

- **Priority:** Medium  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
PERMANENT_SESSION_LIFETIME and REMEMBER_COOKIE_DURATION are set to 14 days but inert: session.permanent is never set and login_user() is never called with remember=True, so sessions are browser-session cookies and no remember cookie is issued.

**Where**
auth.py:492-495, auth.py:292, auth.py:325

**Why this matters**
Owner expects a 14-day session per config; actual behavior silently differs and config misleads maintainers.

**Acceptance criteria**
- Either set session.permanent (and/or remember=True) so the 14-day lifetime applies, or delete the dead config
- Test asserts cookie expiry behavior matches the decision

**Source**
PRD [01-auth-and-account.md § V-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/01-auth-and-account.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 35. Redact raw send exception details from push test responses

- **Priority:** Medium  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Non-410 `pywebpush` exceptions are returned to the client as `error: str(exc)`. Depending on dependency behavior, that may expose provider response details not meant for UI.

**Where**
`app.py:13997`, `app.py:14073`

**Why this matters**
Notification delivery errors should be actionable without leaking raw push-service internals.

**Acceptance criteria**
- Return stable public error codes/messages for send failures.
  - Log detailed exception server-side only, without subscription secrets.
  - Tests cover sanitized non-410 exception response.

**Source**
PRD [12-push-notifications.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/12-push-notifications.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 36. Resolve JSON settings vs SQL settings default drift

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
App JSON settings default to `strength_hypertrophy`, 3 sessions/week, 75 minutes, and 18% body fat, while SQL `user_settings` defaults to `body_recomp`, 4 sessions/week, 60 minutes, and 12% body fat. It is unclear which store is authoritative for new features.

**Where**
app.py settings defaults; data_store.py:1900

**Why this matters**
New routes or agents using the SQL helper can generate different coaching behavior than the dashboard.

**Acceptance criteria**
- Declare the authoritative settings store and defaults.
  - Align helper defaults or add an explicit migration/adapter.
  - Tests assert both app and data-store settings callers return the same defaults where they overlap.

**Source**
PRD [14-data-layer-persistence.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 37. Add contract tests for advanced analytics thresholds

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/analytics/advanced` combines volume landmarks, HRV trend, sleep debt, soreness decay, RPE, deload detection, and settings thresholds, but no focused tests were found for the formula or zone boundaries.

**Where**
`app.py:15886`

**Why this matters**
This endpoint can recommend deload/light/recovery states; threshold drift would affect training decisions.

**Acceptance criteria**
- Tests cover default volume landmark zones at boundary values.
  - Tests cover fatigue score factors and cap behavior.
  - Tests cover `fatigue_threshold` and `deload_recommended`.
  - Malformed or missing settings fall back predictably.

**Source**
PRD [16-progress-analytics-body.md § IC-11](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 38. Align body-fat-only saves with API validation

- **Priority:** Medium  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
The Body tab allows a save attempt when either weight or body-fat percent is present, but `/api/add-body-measurement` requires `weight_lbs`. A user entering only body fat gets a generic save failure instead of a clear validation rule.

**Where**
`static/js/app.js:6959`, `app.py:8550`

**Why this matters**
The app invites an action the backend rejects, creating avoidable friction in the body log.

**Acceptance criteria**
- Client and server agree whether body-fat-only entries are allowed.
  - If weight remains required, the UI blocks body-fat-only saves with specific copy.
  - If body-fat-only is allowed, the API accepts `weight_lbs: null` and charts handle it.

**Source**
PRD [16-progress-analytics-body.md § IC-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 39. Fix bedtime consistency for midnight wraparound

- **Priority:** Medium  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
Bedtime consistency converts sleep-start timestamps to minutes since midnight and uses plain population standard deviation. Bedtimes around midnight, such as 23:50 and 00:10, can appear far apart even though they are close in real behavior.

**Where**
`app.py:15853`

**Why this matters**
The consistency score can penalize normal late-night timing and mislead recovery interpretation.

**Acceptance criteria**
- Bedtime variance uses circular time or a documented anchor window.
  - Overnight edge cases around midnight have tests.
  - Response copy/field name reflects what is actually measured.

**Source**
PRD [16-progress-analytics-body.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 40. Harden Markdown export against partial workout rows

- **Priority:** Medium  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/export-md` indexes `exercise["machine"]`, `s["weight_lbs"]`, and `s["reps"]` directly. Legacy, Apple Health-derived, or partially synced rows can lack those keys and cause export failure.

**Where**
`app.py:15367`

**Why this matters**
Export should be a dependable escape hatch for local-first data, not fail on one imperfect row.

**Acceptance criteria**
- Export uses safe getters and marks missing values as blank or `N/A`.
  - Export skips or labels non-strength/watch-only rows according to a documented rule.
  - Tests cover complete, partial, and empty workout histories.

**Source**
PRD [16-progress-analytics-body.md § IC-10](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 41. Preserve manual sleep visibility when Oura rows exist

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/sleep/analytics` reads Oura long-sleep rows first and falls back to manual `SLEEP_DATA` only when Oura returns no rows. Manual imports are completely hidden when any Oura sleep history exists, even for dates Oura is missing.

**Where**
`app.py:15814`

**Why this matters**
The owner can import local sleep data and still never see it in analytics, making the import path feel broken.

**Acceptance criteria**
- Sleep analytics defines source precedence per date, not all-or-nothing by table.
  - Response includes source provenance for every sleep row.
  - Manual-only dates remain visible when Oura has other dates.
  - Tests cover Oura/manual overlap and gap-fill behavior.

**Source**
PRD [16-progress-analytics-body.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 42. Validate body measurement date and tape fields

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
The body add route validates weight, body-fat, and notes, but stores `date`, `neck_in`, `waist_in`, `chest_in`, `hips_in`, `arms`, and `legs` as passthrough values. Bad strings or impossible values can later appear in charts, backups, or body recomposition payloads.

**Where**
`app.py:8550`

**Why this matters**
Progress analytics are only trustworthy if measurement data has stable units and valid ranges.

**Acceptance criteria**
- `date` accepts only `YYYY-MM-DD` or a documented ISO date contract.
  - Tape fields are numeric inches with explicit min/max ranges or explicitly unsupported.
  - Invalid fields return structured `invalid_field` errors.
  - Existing legacy rows continue to render safely.

**Source**
PRD [16-progress-analytics-body.md § IC-2](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 43. Add cross-source ordering tests for food, WHOOP, and Open Wearables

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Trustworthy Daily Brief

**Problem**
The smart route applies base recommendation logic, WHOOP, Open Wearables, food adaptation, then WHOOP workout patching again. Existing focused tests cover each subsystem, but no observed test locks the final ordering when multiple conservative sources fire together.

**Where**
app.py:14215; whoop_recommendations.py:181; workout_adaptation.py:170

**Why this matters**
Ordering bugs can double-apply volume reductions or hide the true reason a plan became conservative.

**Acceptance criteria**
- Integration test seeds Oura, WHOOP, Open Wearables, and accepted food signals together.
  - Final response proves each modifier is applied at most once.
  - Reasoning and source proof preserve all contributing sources in deterministic order.

**Source**
PRD [11-ai-coach-recommendations.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/11-ai-coach-recommendations.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 44. Add offline dashboard state QA beyond service-worker fallback

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Trustworthy Daily Brief

**Problem**
The service worker returns visible 503 responses offline, but dashboard card-level offline behavior is not comprehensively tested. Existing coverage focuses on retry chips and stale cache prevention.

**Where**
`static/js/sw.js:1`, `static/js/app.js:149`, dashboard render paths

**Why this matters**
The owner may open the PWA in the gym with intermittent connectivity and needs clear degraded states.

**Acceptance criteria**
- Simulate offline API failures for dashboard, Oura, smart recommendation, and trends.
  - Verify retry chips, placeholders, and no stale prior-session guidance.
  - Verify active workout progress prevents forced reload during service-worker update.

**Source**
PRD [02-daily-brief-dashboard.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/02-daily-brief-dashboard.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 45. Add browser QA for active workout modal states

- **Priority:** Medium  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Workout Execution Reliability

**Problem**
Active workout has many critical states: recovered draft, dirty close guard, swap, adjust, set completion, cardio completion, save error, queued, conflicted, and saved. Current coverage is strong but mostly Node/source-level rather than visual/mobile browser QA.

**Where**
`templates/index.html:1614`, `static/js/app.js:7578`, active workout modal

**Why this matters**
Workout execution is the core mobile flow and layout/state regressions directly affect logging.

**Acceptance criteria**
- Browser QA covers active workout modal on mobile viewport.
  - Include swap, adjust, delete/remove, queued, conflicted, empty, blocked, and warning states.
  - Verify bottom nav/modal controls do not overlap.

**Source**
PRD [03-workout-planning-execution.md § IC-9](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/03-workout-planning-execution.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 46. Replace history delete-by-index with stable ids

- **Priority:** Medium  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Workout Execution Reliability

**Problem**
Delete-history accepts a type plus index in the current sorted list, then mutates the underlying original array. If the list changes between render and delete, the wrong row can be removed.

**Where**
`app.py:14637`, `app.py:14684`, `/api/delete-history`, `/api/restore-history`

**Why this matters**
Workout history is user-owned health data; accidental deletion breaks trust even with restore.

**Acceptance criteria**
- History rows expose stable ids for workout/cardio/recovery entries.
  - Delete/restore operate by stable id and type, not sorted index.
  - UI still supports undo using the returned deleted payload.

**Source**
PRD [03-workout-planning-execution.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/03-workout-planning-execution.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 47. Add live sidecar smoke coverage outside unit fixtures

- **Priority:** Low  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
Tests cover the local contracts heavily with mocked sidecar responses, but there is no owner-safe live sidecar smoke procedure in this repo. Real hub catalog, invitation code, and auth payload drift would be caught only during manual operation.

**Where**
Open Wearables setup/sync routes; open_wearables_adapter.py

**Why this matters**
Open Wearables is an external moving part; connector readiness can break even when local unit tests pass.

**Acceptance criteria**
- Define a secret-safe live smoke command or checklist for a local sidecar.
  - Verify provider catalog, setup check, metadata sync, and one blocked/one ready provider path.
  - Store no raw health payloads or secrets in smoke artifacts.
  - Document expected stable error codes for unavailable connectors.

**Source**
PRD [10-open-wearables-integration.md § IC-9](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/10-open-wearables-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 48. Clarify tomorrow-date tolerance in CSV imports

- **Priority:** Low  |  **Labels:** none  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
CSV dates later than tomorrow reject, but tomorrow is allowed. This appears to handle timezone-adjacent data, but the product behavior is not documented in the UI or top-level docs.

**Where**
app.py `_validate_imported_whoop_local_date`; WHOOP modal import copy

**Why this matters**
A future-date row can look surprising in a daily recovery dashboard unless the owner understands the timezone tolerance.

**Acceptance criteria**
- Document the one-day future tolerance as timezone protection or tighten it if unintended.
  - Add UI import help text or import-result warning when tomorrow-dated rows are accepted.
  - Keep tests covering farther-future rejection.

**Source**
PRD [09-whoop-integration.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/09-whoop-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 49. Redact upstream Oura error detail before UI display

- **Priority:** Low  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
Oura sync can include up to 200 characters of upstream response body in an owner-visible error message.

**Where**
`app.py:13480`, `static/js/app.js:6977`

**Why this matters**
Upstream errors are usually harmless, but owner-visible operational details should not accidentally include sensitive provider text.

**Acceptance criteria**
- Map Oura upstream failures to stable public error codes and short public messages.
  - Keep full upstream detail server-side only if needed.
  - Add a regression test that response bodies do not include token-like or raw upstream payload content.

**Source**
PRD [07-oura-integration.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/07-oura-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 50. Rename or clarify `/api/health/sync` ownership

- **Priority:** Low  |  **Labels:** none  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
`/api/health/sync` sounds like Apple Health, while the current route manually pulls Open Wearables data and returns `source: "open_wearables"`.

**Where**
`app.py:11609`, `health_ingest.py:134`

**Why this matters**
Agents and maintainers can wire the wrong sync contract when working on Apple Health.

**Acceptance criteria**
- Document `/api/health/sync` as Open Wearables-owned or alias it to an Open Wearables-specific route.
  - Keep legacy consumers working.
  - Add route comments/tests that prevent Apple Health webhook assumptions.

**Source**
PRD [08-apple-health-integration.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/08-apple-health-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 51. Surface staleness watchdog evidence in Settings

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Integration Confidence

**Problem**
The launchd watchdog writes staleness state to `/tmp/apple-health-staleness.log` and exits nonzero, but the Settings panel does not read or explain the watchdog result.

**Where**
`scripts/check-apple-health-staleness.sh:10`, `static/js/app.js:6761`

**Why this matters**
The owner may see stale Apple Health data without knowing whether the local watchdog is installed, running, or alerting.

**Acceptance criteria**
- Add a safe status endpoint or local summary for watchdog last check/result.
  - Show watchdog state in the Apple Health Settings detail panel.
  - Add tests for no-first-sync quiet state, OK state, stale state, and parse-error state.

**Source**
PRD [08-apple-health-integration.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/08-apple-health-integration.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 52. Surface provider fallback details in the review card

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Photo Food Logging

**Problem**
LM Studio records `_meta` with candidate role/model/fallback-used internally, but the public response only exposes provider and confidence. The user cannot tell whether the estimate came from primary, low-memory, or fallback hardware.

**Where**
`app.py:7434`, `local_vision_adapter.py:270`, `static/js/app.js:11260`

**Why this matters**
Hardware fallback affects latency and trust during local model operation; surfacing a safe label helps debug without reading logs.

**Acceptance criteria**
- Public response includes safe candidate role such as `primary`, `low_memory`, or `fallback`.
  - UI shows a compact "local vision: fallback" indicator only when non-primary was used.
  - No raw URL, prompt, image, or private model trace is exposed.

**Source**
PRD [05-photo-food-logging-vision.md § IC-5](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/05-photo-food-logging-vision.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 53. Add TTL or invalidation policy for food lookup caches

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Branded and barcode lookup caches store fetched responses and timestamps, but no TTL or invalidation policy is evident in the data-store layer.

**Where**
data_store.py:382; data_store.py:391

**Why this matters**
Nutrition provider corrections may not reach the owner if stale cached rows are reused indefinitely.

**Acceptance criteria**
- Define cache TTL or manual refresh semantics for branded and barcode lookup caches.
  - Ensure refresh events record old/new values when stale cache data changes.
  - Tests cover expired cache, fresh cache, and forced refresh behavior.

**Source**
PRD [14-data-layer-persistence.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 54. Add an operator status command for launchd and smoke readiness

- **Priority:** Low  |  **Labels:** Feature  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Operators currently combine `launchctl`, `lsof`, `curl`, logs, and smoke scripts manually to understand runtime health.

**Where**
`scripts/install-launchd-agents.sh`, `support/self_test.sh`

**Why this matters**
A single status command would reduce mistakes during release and rollback.

**Acceptance criteria**
- Provide a read-only status script that reports launchd loaded/running state, listener PID, data dir, last staleness log line, and smoke prerequisites.
  - Do not print secrets, session cookies, or token-bearing URLs.
  - Tests cover output redaction and missing-service states.

**Source**
PRD [15-ops-deployment.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/15-ops-deployment.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 55. Add explicit tests for owner-only non-owner denial

- **Priority:** Low  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The owner guard is central to single-owner privacy, but the listed auth tests do not directly exercise an authenticated non-owner row being denied API and browser routes.

**Where**
`auth.py:249-258`, `auth.py:532-536`

**Why this matters**
Most app data stores are shared local files, so this guard is the main per-account privacy boundary.

**Acceptance criteria**
- Test browser route non-owner denial returns HTTP 403.
  - Test API non-owner denial returns JSON HTTP 403.
  - Test `FITNESS_DASHBOARD_SINGLE_USER=false` intentionally permits non-owner access.

**Source**
PRD [01-auth-and-account.md § IC-6](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/01-auth-and-account.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 56. Add shellcheck/static validation for ops shell scripts

- **Priority:** Low  |  **Labels:** Smoke/QA  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
Shell scripts are central to deployment, but tests mainly exercise selected behavior. There is no static shell lint gate.

**Where**
`scripts/*.sh`, `.githooks/*`, `support/self_test.sh`

**Why this matters**
Small quoting or portability errors can break the local runtime controls.

**Acceptance criteria**
- Add a documented shell static check using an existing available tool or a no-new-dependency alternative.
  - Cover launchd installer, staleness checker, worktree guard, install guard, pre-push hook, post-checkout hook, and self-test.
  - Document any intentionally ignored warnings.

**Source**
PRD [15-ops-deployment.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/15-ops-deployment.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 57. Remove unsourced public metrics and testimonials or back them

- **Priority:** Low  |  **Labels:** none  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
The landing page includes static social proof, named testimonials, and an `87%` volume-goal statistic, but no source, fixture, or calculation exists in the repo.

**Where**
`templates/landing.html:474-490`, `templates/landing.html:631-660`

**Why this matters**
Public marketing copy should not imply measured outcomes that the product cannot substantiate.

**Acceptance criteria**
- Replace unsupported claims with product capability copy, or document and link a real source.
  - Add a static-content test to catch reintroduction of unsourced metrics.
  - Keep single-owner/local-first positioning consistent with current product state.

**Source**
PRD [13-billing-stripe-landing.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/13-billing-stripe-landing.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 58. Validate stored permission_state values

- **Priority:** Low  |  **Labels:** Data contract  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
`permission_state` is stored directly from the request without enum validation. Current UI sends `granted`, but corrupted or unexpected values could distort support-state preview logic.

**Where**
`app.py:14028`, `data_store.py:149`

**Why this matters**
Notification status should be trustworthy because it controls user setup guidance.

**Acceptance criteria**
- Accept only `granted`, `denied`, `default`, or null.
  - Unknown values are dropped or normalized to null.
  - Tests cover invalid value handling and preview support state.

**Source**
PRD [12-push-notifications.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/12-push-notifications.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 59. Wire or remove orphaned clear_food_logs/delete_user_data helpers

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Product Hardening

**Problem**
clear_food_logs and delete_user_data have no product callers (tests only). Either an account-data deletion/reset product flow is missing, or these are dead code that will drift from the live schema.

**Where**
data_store.py:1666, data_store.py:1955

**Why this matters**
Dead data-mutation helpers are a foot-gun and imply a deletion feature that does not exist.

**Acceptance criteria**
- Decide: expose a product flow (owner-only, confirmed) or delete the helpers
- Docs updated to match the decision

**Decision**
FIT-318 deletes both test-only helpers. No account-data deletion/reset product flow is exposed; tests use feature-specific deletion paths where scenario setup requires cleanup.

**Source**
PRD [14-data-layer-persistence.md § V-1](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/14-data-layer-persistence.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 60. Align progress insight type names with frontend styling

- **Priority:** Low  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
The backend emits `positive` and `negative` insight types, while the frontend maps `success`, `warning`, `danger`, and `info`. Positive and negative insights therefore fall through to info styling/icons.

**Where**
`app.py:15411`, `static/js/app.js:5285`

**Why this matters**
Important progress and risk cards lose visual priority.

**Acceptance criteria**
- Frontend maps `positive` to positive styling and `negative` to negative styling, or backend emits frontend-supported names.
  - Tests cover all emitted insight types.
  - Existing empty-state behavior remains unchanged.

**Source**
PRD [16-progress-analytics-body.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 61. Make progressive overload either visible or explicitly API-only

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/progressive-overload` returns fixed-exercise top-set trends, but no current app-shell consumer was found. It also ignores exercises outside the hardcoded eight-machine list.

**Where**
`app.py:14560`, `static/js/app.js`

**Why this matters**
A progress endpoint that is not visible and only covers part of the exercise library can misrepresent strength progression.

**Acceptance criteria**
- Product decision recorded: visible Stats/Body card or API-only.
  - If visible, UI explains covered exercises and empty states.
  - Endpoint either derives exercises from library/settings or documents the fixed list.
  - Tests cover included, excluded, and empty-history cases.

**Source**
PRD [16-progress-analytics-body.md § IC-9](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 62. Productize the Navy calculator and tape-measure flow

- **Priority:** Low  |  **Labels:** Feature  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/body/navy-calc` implements a real body-fat calculator, but the app-shell Body tab exposes only weight and body-fat percent fields. The composition area can display measurements, but the user cannot enter the tape fields or invoke the calculator from the UI.

**Where**
`app.py:15757`, `templates/index.html:580`

**Why this matters**
A useful body-composition helper exists but is effectively hidden from the owner workflow.

**Acceptance criteria**
- Body tab includes a compact tape-measure entry/calculator flow or the endpoint is documented as API-only.
  - Calculated `body_fat_pct` can be reviewed before save.
  - Male/female formula inputs and validation errors are visible.
  - Saved rows preserve tape fields with validated units.

**Source**
PRD [16-progress-analytics-body.md § IC-3](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 63. Remove raw provider payload from weather response

- **Priority:** Low  |  **Labels:** Privacy  |  **Project:** Fitness Dashboard: Progress Loop

**Problem**
`/api/weather` returns `raw.current_condition` from wttr.in. This is not health data, but it expands the public response contract with provider-specific fields the UI does not need.

**Where**
`app.py:10126`

**Why this matters**
Local-first APIs should expose the smallest useful payload, especially for contextual data fetched from external services.

**Acceptance criteria**
- Weather response returns only documented normalized fields used by UI/recommendations.
  - If diagnostics need raw weather, they are behind an explicit debug flag or separate diagnostics route.
  - Tests confirm normalized cache and API responses stay stable.

**Source**
PRD [16-progress-analytics-body.md § IC-12](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/16-progress-analytics-body.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 64. Fix or remove the dashboard RPE chip contract

- **Priority:** Low  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Trustworthy Daily Brief

**Problem**
The dashboard recommendation card tries to read `nw.goal.rpe_target`, but backend `next_workout.goal` is a goal id string while per-exercise RPE targets live on exercise rows. The chip may stay hidden even when a meaningful target exists.

**Where**
`static/js/app.js:2964`, `app.py:3650`, `/api/dashboard`

**Why this matters**
The brief promises training intensity, and RPE is a useful first-screen signal.

**Acceptance criteria**
- Decide whether the dashboard should show average exercise RPE, goal RPE, or no RPE chip.
  - Backend/JS contract exposes the chosen value consistently.
  - Add a test for a plan with exercise RPE targets.

**Source**
PRD [02-daily-brief-dashboard.md § IC-4](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/02-daily-brief-dashboard.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 65. Add visible warning when active workout draft cannot persist

- **Priority:** Low  |  **Labels:** Improvement  |  **Project:** Fitness Dashboard: Workout Execution Reliability

**Problem**
Draft saving catches localStorage errors silently. If storage is unavailable, the user may believe the in-progress workout is recoverable when it is only in memory.

**Where**
`static/js/app.js:7040`, active workout localStorage draft

**Why this matters**
A mobile browser storage failure during a workout can cause lost set notes and logged sets.

**Acceptance criteria**
- Detect draft persistence failure and show a non-blocking warning.
  - Keep current in-memory behavior for the active page.
  - Add a JS test for localStorage failure path.

**Source**
PRD [03-workout-planning-execution.md § IC-7](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/03-workout-planning-execution.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
## 66. Document or fix Plank target weight clamping

- **Priority:** Low  |  **Labels:** Bug  |  **Project:** Fitness Dashboard: Workout Execution Reliability

**Problem**
The exercise builder special-cases Plank with `target_weight = 0`, but the returned field uses `max(5, target_weight)`, which appears to convert Plank back to 5 lb. It is unclear whether this is intentional.

**Where**
`app.py:3384`, `_build_exercise_entry`

**Why this matters**
Timed bodyweight/core exercises should not show confusing load targets.

**Acceptance criteria**
- Confirm intended Plank/bodyweight display contract.
  - If bodyweight timed work should show no load, return/display 0 or blank consistently.
  - Add a regression test for timed core prescriptions.

**Source**
PRD [03-workout-planning-execution.md § IC-8](https://github.com/villarrealwesley79-del/fitness-dashboard/blob/villarrealwesley79/fit-268-reverse-engineer-per-feature-prds-and-documentation-for/docs/prd/03-workout-planning-execution.md) — reverse-engineered documentation pass FIT-268 (PR #226). Verification: CONFIRMED by independent code review.

---
