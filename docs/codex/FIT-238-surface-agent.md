# FIT-238 Product Surface Inventory And Verification Plan

Read-only inventory for `FIT-238` in `/Users/admin/codex-worktrees/fitness-fit-238-qol`. Product code was not edited.

## Scope

- Inputs read: `README.md`, `docs/superpowers/plans/2026-06-25-fit-238-quality-of-life-pass.md`, `templates/*.html`, `static/js/app.js`, `static/css/style.css`, selected `tests/*.py`, and prior fitness-dashboard audit memory.
- Constraint: do not edit product code; only document practical user-facing surface area and a finite validation path.
- Important implementation shape: the product is mostly one authenticated route, `/`, with eight client-side tabs inside `templates/index.html`. Route-only smoke checks are not enough.

## Current Local State

- Worktree branch: `villarrealwesley79/fit-238-one-time-quality-of-life-pass-for-core-fitness-dashboard`.
- `auth.db` currently has `0` users.
- `fitness_data.db` currently has `0` `food_logs`, `0` `body_data`, `0` `cardio_data`, `0` `recovery_data`, `2` `workout_adaptation_events`, `1` `meal_review_snapshots`, and `0` `push_subscriptions`.
- `apple_health_sync.db` currently has `0` sync rows.
- `oura_daily.sqlite3` currently has `0` Oura rows.

Implication: most high-value product states are not practically verifiable in this worktree without either:

1. creating or seeding local auth/data state, or
2. running an isolated test instance with disposable DBs / `LOGIN_DISABLED=True`.

Because this is a shared worktree and runtime DBs live in-repo here, I would not use the empty local DBs for ad hoc manual mutation on the main FIT-238 branch.

## Route Inventory

### Public Pages

| Route | Surface | Practical local status | Notes |
| --- | --- | --- | --- |
| `/login` | Sign-in form | Testable now | Server-rendered form with flash errors, CSRF token, and redirect-on-success flow. |
| `/register` | Registration form | Testable, but mutates local auth DB | Currently open because `auth.db` has `0` users; once a user exists, single-owner mode can block it. |
| `/pricing` | Pricing / checkout entry | Testable now | Static render is easy; real checkout requires login plus Stripe env. |
| `/success` | Post-checkout success page | Testable now | Static page. |
| `/cancel` | Post-checkout cancel page | Testable now | Static page. |

### Authenticated Pages

| Route | Surface | Practical local status | Notes |
| --- | --- | --- | --- |
| `/` | Main app shell | Blocked by missing auth + seed data | One route contains Dash, Vitals, Next, Log, History, Body, Stats, Settings. |
| `/gym-now` | Emergency workout fallback page | Blocked by missing auth + next-workout data | Useful mobile/PWA fallback path; should be in FIT-238 validation scope. |

Excluded from product-surface scope:

- `/test-chart` is a dev helper, not a practical user-facing flow.
- `manifest.json`, `sw.js`, and the Stripe webhook are product plumbing, not primary UX surfaces.

## Coverage Map

### Auth Shell Navigation

- Bottom tab bar with eight tabs: `Dash`, `Vitals`, `Next`, `Log`, `History`, `Body`, `Stats`, `Settings`.
- Keyboard contract exists for Left/Right/Home/End tab switching and ARIA synchronization.
- Mobile risk area: fixed bottom tab bar plus safe-area padding; verify no overlap with modal sheets or long content.
- Local status: blocked until an authenticated shell is available.

### Dashboard Tab

- Primary controls:
  - AI status popover button in header.
  - Retry chips for readiness, recommendation, and insight.
  - `Start Workout`, `Adjust Plan`, `View food log`.
  - Meal composer with text input, image picker, barcode scan button, barcode manual lookup, retry button, offline banner, and pending-review list.
  - Quick Trends `<details>` expander.
- Overlays and dependent surfaces:
  - Food log modal.
  - Active workout modal via `Start Workout`.
  - Adjust Plan modal via `Adjust Plan`.
  - Pending review cards rendered from meal review snapshots.
- Empty / warning / blocked states:
  - Macro empty state: "No food logged today."
  - Meal composer offline banner.
  - Meal composer error area.
  - Retry chips when dashboard slices fail.
  - Workout adaptation host stays hidden unless an applied change exists.
- Local status now:
  - Static shell review only.
  - Pending-review UI is plausibly seedable from the existing `meal_review_snapshots` row.
  - Real food-log, composer success, and workout-start flows need auth and disposable state.

### Vitals Tab

- Read-only metric cards for core metrics, activity, sleep, and body.
- Mini-spark charts for steps, active minutes, and sleep.
- Main user-visible risk is placeholder handling and stale/missing wearable data.
- Local status now: blocked for meaningful validation because there is no Oura or Apple Health data in this worktree.

### Next Workout Tab

- Primary controls:
  - `Start Workout`.
  - `Adjust Plan`.
- Display surfaces:
  - recommended plan hero,
  - exercise list,
  - optional cardio finisher card.
- Empty / warning states:
  - "No exercises planned - rest day."
  - degraded settings / recommendation fallback path in JS.
- Local status now: blocked by missing auth and next-workout seed state.

### Log Tab

- Segmented control for `Strength`, `Cardio`, `Recovery`.
- Forms:
  - `form-strength`
  - `form-cardio`
  - `form-recovery`
- Controls:
  - exercise selector,
  - RPE button grid,
  - numeric/date inputs,
  - notes textareas,
  - `Log Set`, `Log Cardio`, `Log Recovery`.
- Summary card at bottom.
- Local status now:
  - structure is locally inspectable.
  - actual logging mutates shared DB state, so browser validation should happen only on an isolated disposable DB copy.

### History Tab

- Controls:
  - range chips `7D`, `14D`, `30D`, `90D`, `1Y`,
  - type filter chips for recent workouts.
- Display surfaces:
  - frequency chart,
  - volume chart,
  - top exercises,
  - recent workout list.
- Dependent modals:
  - workout detail modal,
  - analyze modal,
  - delete confirmation modal.
- Empty / warning states:
  - no workouts in range,
  - filtered-empty state,
  - chart unavailable fallback,
  - no exercises in range.
- Local status now: blocked because there are no workouts in local DB.

### Body Tab

- Display surfaces:
  - weight/body-fat cards,
  - hidden interpretation card,
  - hidden target-progress card,
  - weight and body-fat charts,
  - composition grid,
  - hidden 14-day nutrition trend card.
- Forms and drill-ins:
  - `form-body` measurement entry,
  - expandable per-day nutrition rows,
  - meal detail modal,
  - meal correction form,
  - destructive meal delete.
- Empty / warning states:
  - no meal rows for a day,
  - photo-retention note,
  - stub-photo notice for non-real photo analysis cases.
- Local status now: blocked because there are no `body_data` or `food_logs` rows.

### Stats Tab

- Controls:
  - range chips `7D`, `14D`, `30D`, `90D`, `1Y`.
- Display surfaces:
  - KPI cards,
  - muscle recovery grid and detail pane,
  - volume-by-muscle donut,
  - insights list.
- Empty / warning states:
  - muscle recovery empty state: "No fatigue data yet - log a workout to populate."
  - generic chart empty/no-data fallbacks in JS.
- Local status now: blocked because history/workout data is absent.

### Settings Tab

- Coaching setup:
  - goal cards,
  - DOB, sex, preferred duration, sessions-per-week slider, equipment selector.
- AI coach:
  - headline state row,
  - flaky warning row,
  - primary/fallback host rows,
  - 24h activity row.
- Data sources:
  - Oura state + Sync button,
  - Apple Health state + Setup button,
  - Weather chip.
- Notifications:
  - Enable / Send test / Disable buttons,
  - Add to Home Screen warning,
  - blocked / revoked / VAPID-not-configured warning rows,
  - active alerts and test-delivery rows.
- Maintenance:
  - export,
  - import backup,
  - last backup,
  - learned vocabulary list,
  - sign out.
- Dependent modal:
  - Apple Health setup modal.
- Local status now:
  - shell review is possible only after auth exists.
  - most interesting states also need env, browser permission, push support, or wearable data.

## Modal / Detail / Panel Inventory

### Modals

- `modal-swap`
- `modal-adjust`
- `modal-analyze`
- `modal-workout-detail`
- `modal-meal-detail`
- `modal-food-log`
- `modal-delete-confirm`
- `modal-apple`
- `modal-active`
- `modal-workout-saved`
- `modal-sync-queue`

### Other Interactive Panels

- Header AI status popover.
- Log-tab segmented panels for strength/cardio/recovery.
- Dashboard Quick Trends `<details>`.
- Body-tab expandable nutrition rows.

### Keyboard / Focus Expectations

- Tab bar arrow-key navigation is explicitly wired.
- Modal escape and focus-return behavior are explicitly wired and already contract-tested.
- Review cards are focusable groups with ARIA relationships.
- This still needs real-browser validation for focus visibility, return-focus targets, and stacked modal behavior.

## Empty, Warning, And Blocked States That Matter For FIT-238

### Public/Auth States

- Login invalid credentials.
- Login rate limit.
- Login service unavailable.
- Registration disabled for single-owner mode.
- Registration validation errors.
- Pricing flash when Stripe is not configured.

### App Shell States

- Dashboard retry chips for failed slices.
- Macro empty state and food-log empty state.
- Meal composer offline / retry / barcode-not-found / barcode-unavailable paths.
- History empty state and filtered-empty state.
- Stats muscle-recovery empty state.
- AI coach flaky warning row.
- Apple Health stale row.
- Push blocked / revoked / install-required / VAPID-misconfigured rows.
- Analyze-modal error state.
- Sync queue modal.

## Mobile Views To Cover

The CSS is mobile-first and has explicit breakpoints around `640px`, `480px`, `420px`, and `360px`. FIT-238 should treat the following as required mobile checks:

- Bottom tab bar fit and tapability at `390x844` and `360x800`.
- Dashboard meal composer reflow:
  - at <= `640px`, submit button compresses;
  - at <= `420px`, input and submit stack.
- Modal-sheet height and bottom safe-area padding.
- Food-log refresh toast width bounds at ~`92-94vw`.
- Body nutrition summary compaction at <= `480px`.
- Readiness gauge + stats layout at <= `360px`.
- Muscle-recovery grid collapsing to one column on small screens.

## What Is Testable Locally Right Now

### Without Auth Or Seeding

- `/login`
- `/pricing`
- `/success`
- `/cancel`
- template/static contract tests that inspect markup and JS directly

Recommended local non-browser checks already present:

- `tests/test_fit192_accessibility_contract.py`
- `tests/test_dashboard_render_contract.py`
- `tests/test_history_detail_and_analyze.py`
- `tests/test_fit142_barcode_ui.py`
- `tests/test_fit139_refresh_ui.py`
- `tests/test_fit187_active_workout_start_guard.py`
- `tests/test_fit219_review_card_a11y.py`

### With Auth But Still Mostly Empty Data

- Base shell navigation on `/`
- `/gym-now` shell if a recommendation can be generated
- Settings shell and static warnings

### Requires Seeded State Or Disposable DB Data

- Dashboard macro card with real meals
- Pending meal review cards
- Active workout flow
- Log tab real submissions
- History charts and workout detail/analyze/delete
- Body charts, nutrition trend rows, meal detail/correction/delete
- Stats charts and muscle-recovery detail

### Requires Credentials, Env, Hardware, Or Browser Permissions

- Stripe checkout start: login plus Stripe env.
- Oura sync / freshness validation: token plus data.
- Apple Health setup validation beyond static modal copy: real sync token + HAE sender.
- Push notifications: supported browser plus permission state and VAPID configuration.
- Barcode camera scan: camera device and browser permission.
- PWA / Add to Home Screen / stale-cache fallback: iPhone Safari or equivalent installable context.

## Top Quality Risks

1. The main product is a single authenticated shell with many modal and hidden-state branches, so route smoke alone will miss the failures FIT-238 is supposed to catch.
2. This worktree currently has no auth user and almost no seed data, so "local validation" is mostly blocked unless the runner uses isolated disposable DBs.
3. Several high-risk flows are destructive or mutating: start/complete workout, delete workout, delete meal, save correction, import backup, settings changes. They should not be exercised against shared runtime files.
4. Mobile quality is easy to regress because the app uses a fixed bottom tab bar, stacked modal sheets, long-card content, and breakpoint-specific meal composer/layout behavior.
5. Integration state is user-visible in Settings but depends on external conditions: AI host reachability, Apple/Oura freshness, push permissions, and Stripe config.

## Suggested Finite E2E Validation Path

Use an isolated local instance, not the shared in-repo DBs. Either copy the SQLite files to temp paths and point the app at those, or run a test-oriented instance with `LOGIN_DISABLED=True` plus explicit seed fixtures.

### 1. Public-route pass

1. Open `/login`.
2. Tab through username, password, and submit to verify focus visibility.
3. Open `/register`; verify validation and the single-owner caveat path.
4. Open `/pricing`; verify both plan cards, the CSRF-backed checkout form, and the back link.
5. Open `/success` and `/cancel`.

### 2. Authenticated desktop pass

Seed requirements:

- 1 owner user
- 1 next-workout recommendation
- 2 completed workouts
- 2 body entries
- 2 food logs
- 1 pending meal review snapshot
- 1 adaptation event

Validation path:

1. Log in and land on `/`.
2. Use keyboard arrows on the tab bar to traverse all eight tabs.
3. On `Dash`, open and close the AI status popover, then open and close the food-log modal with `Escape` and verify focus returns to `View food log`.
4. Still on `Dash`, verify macro-filled and macro-empty behavior, pending meal review card presence, meal composer draft behavior, and the Quick Trends `<details>` expander.
5. On `Next`, click `Start Workout` to open `modal-active`, then open `Adjust Plan`, use one preset, cancel once, then apply once on the disposable DB.
6. On `Log`, switch Strength -> Cardio -> Recovery; verify each panel's fields and validation affordances. Perform at most one real disposable submission.
7. On `History`, change range chips, open a workout detail modal, run `Analyze workout`, then open `Delete Workout?` and cancel.
8. On `Body`, open a nutrition row, open meal detail, enter correction mode, then cancel; do not save unless the DB is disposable.
9. On `Stats`, change ranges and verify both filled and empty handling.
10. On `Settings`, open Apple Health setup, inspect AI coach rows, inspect push warning rows, and verify export/import/personal-vocab surfaces render.

### 3. Mobile pass

Run the same shell at `390x844` and `360x800`.

Required checks:

1. Bottom tab bar stays visible and does not overlap modal actions.
2. Dashboard meal composer stacks correctly at narrow widths.
3. Active workout modal remains scrollable with visible primary actions.
4. History detail and meal-detail modals fit within the viewport.
5. Settings push rows and Apple setup modal do not overflow horizontally.

## Known Blockers And Caveats

- Current worktree auth/data is not sufficient for a meaningful browser audit.
- `support/self_test.sh` is useful only after a running local server and valid owner credentials exist.
- Registration is technically possible with `0` users, but that mutates a shared worktree DB and should not be the default FIT-238 validation path.
- Push, Apple Health, Oura, Stripe, and barcode camera validation all need runtime conditions that are absent in this worktree.
- The app already has strong static contract coverage for accessibility and state-shape regressions, but that does not replace the modal/mobile/browser checks above.
