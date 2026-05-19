# Fitness Dashboard PRD

Status: Draft  
Last updated: 2026-05-18
Owner: Wesley  
Product type: Single-owner mobile-first fitness coaching dashboard

## 1. Problem

The owner has training history, body data, Oura recovery metrics, Apple Health activity data, soreness notes, food intake, and goal settings spread across multiple sources. The current app already collects and displays much of this, but the product direction needs to be clearer: it should turn those signals into a daily training and nutrition decision, guide the workout, account for meals and snacks, and close the loop after completion.

The product problem is not "show more metrics." The problem is making training, recovery, and food decisions easier from the data already available.

## 2. Goals

- Produce a trustworthy daily workout recommendation based on recovery, soreness, recent training, equipment, goals, and available time.
- Make mobile workout execution fast: start, swap, log sets, complete, and analyze.
- Let the owner log food by taking a photo, then auto-estimate calories/macros with confidence and correction states.
- Auto-adjust remaining daily nutrition targets and coaching context after meals, snacks, or missed food.
- Keep deterministic safety rails in charge of prescription and use AI only for explanation, analysis, and constrained adjustment.
- Make wearable sync state honest and actionable.
- Connect workout output to body-composition and recovery trends.
- Maintain a reliable local runtime on the Mac mini with clear verification paths.

## 3. Non-Goals

- Do not replace the deterministic workout engine with an LLM.
- Do not build a public multi-user SaaS until the data model is made per-user.
- Do not treat AI coach text as authoritative if it conflicts with safety rails.
- Do not add public endpoints without token checks or explicit auth review.
- Do not prioritize broad redesign over fixing the core workout execution loop.
- Do not treat photo-based food recognition as exact. It must be editable and confidence-labeled.

## 4. Users

Primary user: the owner, using the app on mobile before and during workouts.

Secondary user: a developer or agent maintaining the app, integrations, and runtime.

## 5. Core User Journeys

### Daily Brief

The owner opens the app and sees readiness, recovery context, food status, recommended workout, avoid list, time estimate, intensity target, and the reason for the recommendation.

Acceptance criteria:

- Recommendation is visible on the first screen.
- Readiness and wearable sync state are visible.
- Recommendation includes a reason, intensity, time estimate, and primary action.
- Today's calories, protein, and remaining macro status are visible when food has been logged.
- If wearable data is stale, the UI says so.

### Photo Food Logging

The owner takes a picture of food, such as popcorn, a meal plate, a shake, or a snack. The app estimates the item, portion, calories, protein, carbs, fat, sodium, and confidence, then lets the owner accept or correct it.

Acceptance criteria:

- User can capture or upload a food photo from mobile.
- User can add a short note to clarify context, such as "large popcorn, shared half" or "homemade bowl."
- App returns a structured nutrition estimate with confidence.
- Low-confidence estimates require review before they affect the daily plan.
- Accepted food logs update daily calories, macros, and remaining targets.
- User can edit portion, item name, calories, protein, carbs, fat, sodium, and meal time.
- The app stores the original estimate, user corrections, and final accepted values.
- The dashboard reflects how the food changed the day, not just that food was logged.

### Start and Complete Workout

The owner starts the recommended workout, logs each set, optionally swaps exercises, completes the workout, and saves it to history.

Acceptance criteria:

- Active workout opens with recommended exercises, reps, weight, sets, and RPE prefilled.
- Exercise swaps work inside the active workout modal.
- Logged set values survive exercise swaps and modal rerenders when possible.
- Completion creates a stable workout ID and updates history.
- Mobile layout does not hide controls behind bottom nav or modal layers.

### Adjust Plan

The owner gives a plain-language constraint such as low time, sore shoulder, no machine available, or low energy. The AI returns an intent patch. Python validates and applies only safe changes.

Acceptance criteria:

- AI output is validated by Python before any change is applied.
- RPE, sets, soreness, deload, readiness, and duration rails are enforced.
- The UI shows what changed and what was refused.
- If LM Studio is unavailable, the deterministic plan remains usable.

### Recovery and Wearable Sync

The owner can tell whether Oura and Apple Health data are fresh enough to trust.

Acceptance criteria:

- Oura status shows cached/live state and latest available day.
- Apple Health status uses backend sync evidence, including last accepted attempt.
- Token-gated sync endpoint rejects missing or invalid tokens.
- Auth-gated status endpoint rejects unauthenticated requests.

### History and Progress Review

The owner reviews workouts, food intake, volume, trends, adherence, and body changes to understand whether the plan is working.

Acceptance criteria:

- History includes manual workouts and synced activity where appropriate.
- Body trend shows weight and body-fat history.
- Stats summarize volume, sets, RPE, time, and muscle distribution.
- Nutrition history summarizes daily calories, protein, carbs, fat, sodium, confidence/correction rate, and adherence to targets.
- AI analysis can summarize a completed workout without mutating the plan.

## 6. Functional Requirements

### Dashboard

- Show daily recommendation, readiness, HRV/RHR/sleep summary, steps/activity, food status, weight/body status, and insight cards.
- Prefer current Oura and Apple Health data when available, with clear stale states.
- Keep the first-screen action focused on starting or adjusting the recommended workout.
- Show food-driven changes to the day's plan, such as calories remaining, protein gap, over-target warnings, or under-fueled training notes.

### Workout Engine

- Generate next workout from history, soreness, goal, available time, and equipment preference.
- Preserve stable workout IDs.
- Support deterministic exercise swaps.
- Support cardio finisher when present in the recommendation.
- Respect volume, RPE, soreness, recovery, deload, and duration constraints.

### Active Workout

- Prefill recommended set rows.
- Let the owner edit weight, reps, RPE, and notes.
- Support in-workout swaps.
- Save a full workout through `/api/complete-workout`.
- Trigger or offer post-workout analysis.

### Food Logging

- Support mobile camera capture and image upload for food.
- Support optional text note for portion/context.
- Use an AI vision estimate to identify likely food items and portion size.
- Normalize the estimate into calories, protein, carbs, fat, sodium, fiber when available, item name, portion description, meal type, timestamp, confidence, and source.
- Require user review for low-confidence estimates.
- Allow quick corrections before saving.
- Save both raw estimate and accepted final values.
- Recompute the daily nutrition summary immediately after save.
- Feed accepted nutrition totals into body recomposition status and daily coaching.

### Auto Adjustment From Food

- If calories are over target, show a body-recomposition warning and adjust remaining-day guidance.
- If protein is below target, prioritize protein guidance in the daily brief.
- If the user is under-fueled before a hard workout, warn or suggest a lighter intensity/food-first action.
- If a high-sodium or late meal is logged, mark it as context for next-day weight/readiness interpretation rather than treating scale change as pure fat gain.
- Never silently change a workout plan solely from an uncertain food estimate.

### Settings

- Support training goal, sessions per week, available time, equipment preference, nutrition targets, food-estimation defaults, and integrations.
- Surface Oura, Apple Health, weather, AI coach status, and backup/import.

### Integrations

- Oura: sync sleep/readiness/activity into SQLite cache and expose status/trends.
- Apple Health: accept Health Auto Export webhook posts through token-gated endpoint and support legacy file exports.
- LM Studio: support primary and fallback routes, health checks, metrics, strict schemas, and graceful fallback.
- Vision food estimator: support a model-backed structured estimate path with strict schema validation and graceful manual fallback.

### Data and Backup

- Keep JSON files and SQLite stores readable and recoverable.
- Export/import backup through the app.
- Avoid exposing raw tokens, raw JSON, or sync-token material through normal API responses.
- Treat food photos as sensitive personal data. Do not expose images or raw model traces through normal API responses.
- Default food photo retention is documented in `docs/FOOD_PHOTO_PRIVACY.md`: discard raw photos after extraction unless a future explicit opt-in retention issue changes that policy.
- Accepted food logs are persisted separately from the daily nutrition summary so multiple meals/snacks can retain final values plus sanitized original estimate and correction metadata. Existing nutrition JSON remains a readable legacy/backfill source.

## 7. Non-Functional Requirements

- Mobile-first UI with safe-area-aware controls.
- Fast enough to use during a workout.
- Auth-gated personal data endpoints.
- Token-gated public webhook endpoints.
- Clear degraded states for missing wearable or AI data.
- Clear confidence and review states for photo food estimates.
- Cache-bust JS/CSS asset versions after structural frontend changes.
- Local dates should use local time, not UTC, for workout and body logs.

## 8. Current MVP Scope

The current MVP is a Flask PWA with:

- 8-tab mobile interface: dashboard, vitals, next workout, log, history, body, stats, settings.
- Oura integration.
- Apple Health Health Auto Export integration.
- Deterministic workout recommendation.
- AI Adjust Plan and Analyze Workout flows.
- Active workout modal with prefilled recommendation values and swap support.
- Body, nutrition, soreness, cardio, recovery, history, stats, and backup surfaces.
- Manual nutrition targets and nutrition log data, but not yet a finished photo-based food capture workflow.

## 9. App Surface Decision

Decision: use the hybrid shortcut/app flow, with the existing Flask PWA as the primary product surface.

- Food camera capture stays PWA-first through mobile camera/file upload in the web app. Do not build native camera capture for the initial food logging milestone.
- Apple Health sync stays bridged through Health Auto Export or Shortcuts-style token-gated webhook posts into the backend. Do not treat the browser/PWA as a direct HealthKit client.
- Notifications should use Home Screen PWA Web Push for low-stakes reminders, stale-data warnings, and review-pending alerts. Do not treat Web Push as safety-critical infrastructure.
- Offline workout logging should use a PWA local queue with stable client-generated workout IDs, idempotent backend sync, visible retry/reconcile states, and no silent double-save behavior.
- Native iOS is deferred to a narrow HealthKit helper only if the Apple Health bridge fails a documented freshness SLA after HAE/Shortcuts instrumentation has been improved.

Tradeoff record:

- Speed: the hybrid path extends the current app and avoids a premature native rewrite.
- Reliability: bridge freshness can be measured through last sync, last accepted attempt, event counts, and stale warnings.
- Privacy: the backend remains canonical and token-gated; raw food photos should be discarded after extraction unless a future issue explicitly opts into retention.
- Maintenance: the project avoids adding Swift, HealthKit permissions, App Store/provisioning, and native test surface before the product loop is hardened.

Platform basis:

- HealthKit is the native framework for Apple health and fitness data access.
- iOS Home Screen web apps support Web Push for app-like notifications.
- Web camera capture and file upload are appropriate for the PWA food-capture path when served from a secure context.
- Service workers support the offline shell, caching, and request-handling foundation needed for queued workout sync.

## 10. Next Product Milestones

### Milestone 1: Trustworthy Daily Brief

- Tighten first-screen hierarchy around the recommended action.
- Show stale-data warnings for Oura and Apple Health.
- Show food status: calories remaining, protein gap, and whether today's intake changed the recommendation.
- Add plain-language reason and avoid-list prominence.
- Add "why no change" copy for empty AI intent patches.

### Milestone 2: Photo Food Logging

- Add PWA-first mobile food camera/upload flow.
- Add AI food estimate schema, confidence labels, and review/edit UI.
- Save accepted food logs and recompute daily nutrition totals.
- Make the dashboard explain what changed after a food entry.

### Milestone 3: Workout Execution Reliability

- Run full mobile visual QA across active workout, swap, adjust, complete, delete, empty, blocked, and warning states.
- Make completion and post-workout analysis visibly connected.
- Add stronger error states for failed saves.
- Add offline workout queue/retry/reconcile behavior after the backend sync contract exists.

### Milestone 4: Progress Loop

- Tie completed workouts to adherence, fatigue, soreness, and next recommendation.
- Tie accepted food logs to calories/macros, body trend, and next-day interpretation.
- Improve history details and workout analysis.
- Make body recomposition progress easier to read.

### Milestone 5: Integration Confidence

- Surface 24-hour AI metrics in Settings.
- Add warnings when fallback rate rises.
- Make Apple Health daily sync schedule and last-attempt evidence easy to inspect.
- Add low-stakes PWA Web Push reminders and stale-data alerts after the backend subscription contract exists.

### Milestone 6: Product Hardening

- Maintain the hybrid PWA plus webhook-bridge app surface decision.
- Clean up legacy files and stale docs.
- Add repeatable authenticated smoke testing.
- Document release and rollback procedure.
- Keep native HealthKit helper work behind the documented bridge freshness SLA trigger.

## 11. Open Product Questions

- Should photo food logging start with rough estimates, or require a nutrition database match before save?
- Should uncertain photo estimates affect the plan immediately, or only after owner confirmation?
- Which nutrition fields are required for auto-adjustment: calories/protein only, or full macros plus sodium/fiber?
- Should the app auto-apply safe plan adjustments, or require one-tap confirmation?
- Should soreness tracking support side-specific joints and injuries?
- Should this remain single-owner permanently?
