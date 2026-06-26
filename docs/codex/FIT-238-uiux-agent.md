# FIT-238 UI/UX Discovery Report

Date: 2026-06-25

Scope: report-only discovery for FIT-238 in `/Users/admin/codex-worktrees/fitness-fit-238-qol`. No product code was edited. To avoid mutating worktree runtime files, the browser pass used `DATA_DIR=/tmp/fit238-audit-runtime` and screenshots were saved under `/tmp/fit238-uiux-audit`.

## Method

- Read: `docs/superpowers/plans/2026-06-25-fit-238-quality-of-life-pass.md`, `README.md`, `/Users/admin/.codex/ui/DESIGN.md`.
- Runtime: local Flask server from the FIT-238 worktree on `http://127.0.0.1:5051`.
- Browser: Chrome via Playwright, desktop `1440x1200` and mobile `iPhone 13` emulation.
- Fresh state: new local auth user in the temp runtime only (`fit238-audit`), then empty-data first-run flow.
- Interaction types covered: clicks, tab-bar keyboard navigation, modal Escape behavior, desktop/mobile, loading/empty/success states where reachable without writing into worktree data files.

## Coverage

Covered surfaces:

- `/login` sign-in screen
- `/register` short-password error and successful first-run account creation in temp runtime
- `/` dashboard shell on desktop and mobile
- Dashboard food-log modal
- Dashboard adjust-plan modal
- Active workout modal opened from `Start Workout`
- History tab empty state
- Body tab empty state
- Settings tab first-run state
- Keyboard tab navigation on desktop (`ArrowRight` + `Enter`)

Passes:

- Desktop tablist keyboard navigation worked and switched tabs as expected.
- Food log and adjust-plan modals closed with `Escape`.
- Active workout close button worked.

## Recommended First FIT-238 Fix

If FIT-238 only takes one scoped fix now, start with **F1: remove the global zoom lock**. It is the smallest safe change, affects the entire authenticated shell plus login, and directly improves mobile accessibility without touching product logic.

## Ranked Findings

### F1. High — Mobile zoom is globally disabled

- Route/screen: `/login`, `/`, and every tab rendered from `templates/index.html`
- Severity: High
- Reproduction:
  1. Open the app on mobile.
  2. Inspect the viewport meta tag or attempt browser zoom.
- Expected: Users can pinch-zoom and use browser text enlargement on dense mobile UI.
- Actual: The shell hard-disables zoom with `maximum-scale=1.0, user-scalable=no`.
- Evidence:
  - Screenshot: `/tmp/fit238-uiux-audit/14-dashboard-mobile.png`
  - Code: `templates/index.html:5`
  - Observed meta content: `width=device-width, initial-scale=1.0, viewport-fit=cover, maximum-scale=1.0, user-scalable=no`
- Safe FIT-238 recommendation:
  - Remove `maximum-scale=1.0, user-scalable=no` from the viewport meta.
  - Re-run the dashboard, active workout modal, and settings screen on mobile to confirm layout still holds at zoomed sizes.

### F2. Medium-High — Active workout modal ignores `Escape`, unlike the other modals

- Route/screen: `/` dashboard -> `Start Workout` -> active workout modal
- Severity: Medium-High
- Reproduction:
  1. Sign in on desktop.
  2. Click `Start Workout`.
  3. Press `Escape`.
- Expected: `Escape` should use the same guarded close path as the close button: close immediately when nothing is dirty, or show the discard confirmation path when progress exists.
- Actual: The modal stays open. Focus moves to the close button, but the modal does not dismiss.
- Evidence:
  - Screenshot: `/tmp/fit238-uiux-audit/18-active-workout-after-escape-desktop.png`
  - Screenshot before interaction: `/tmp/fit238-uiux-audit/08-active-workout-modal-desktop.png`
  - Code: `static/js/app.js:925-947` excludes `modal-active` from generic modal closing and Escape handling.
  - Code: `static/js/app.js:5742-5778` rewires the close button to `cancelActiveWorkout({ requireConfirm: true })`, but no equivalent Escape path exists.
- Safe FIT-238 recommendation:
  - Let `Escape` route through `cancelActiveWorkout({ requireConfirm: true })` for `modal-active`.
  - Keep the current dirty-progress guard; do not make `Escape` a silent destructive close.

### F3. Medium — Dashboard macro-card header has a visible spacing/layout bug on fresh state

- Route/screen: `/` dashboard, macro card
- Severity: Medium
- Reproduction:
  1. Sign in with a fresh account.
  2. Load the dashboard with no food logged.
  3. Inspect the macro card header on desktop or mobile.
- Expected: The label, status text, and `View food log` action should read as separate header elements with deliberate spacing.
- Actual: The empty-state copy renders as `TODAY'S MACROSno entries`, with the subtext jammed directly into the section label.
- Evidence:
  - Screenshots: `/tmp/fit238-uiux-audit/04-dashboard-desktop.png`, `/tmp/fit238-uiux-audit/14-dashboard-mobile.png`
  - Code: `templates/index.html:90-94`
  - Code: `static/js/app.js:1316-1326`
  - Code: `static/css/style.css:311-326`, `static/css/style.css:3274-3279`
- Safe FIT-238 recommendation:
  - Group the label and subtext into a small stack or inline cluster instead of relying on three peer items in `.card-head`.
  - Keep the CTA right-aligned, but ensure the empty-state summary wraps independently from the title.

### F4. Medium — Fresh no-data state is rendered as a load failure in multiple dashboard cards

- Route/screen: `/` dashboard first run
- Severity: Medium
- Reproduction:
  1. Sign in with a fresh account and no wearable data.
  2. Load the dashboard.
- Expected: First-run cards should distinguish “no data / not connected yet” from “endpoint failed to load.”
- Actual: The readiness card and insight card show `Couldn't load · retry` even though the recommendation card already explains the real condition: no recent wearable data and no food logged yet.
- Evidence:
  - Screenshots: `/tmp/fit238-uiux-audit/04-dashboard-desktop.png`, `/tmp/fit238-uiux-audit/14-dashboard-mobile.png`
  - Code: `static/js/app.js:1000-1050` normalizes Oura/reco/sleep failures to `null`
  - Code: `static/js/app.js:1436-1481` treats `null` as an error sentinel and surfaces retry chips
  - Code: `static/js/app.js:1773-1787` paints the retry chips from those sentinels
- Expected vs actual detail:
  - Expected: “Connect Oura/Apple Health” or “No wearable data yet” empty/setup state.
  - Actual: transport-failure language on a valid fresh account.
- Safe FIT-238 recommendation:
  - Split `no data` from `request failed` in the dashboard loaders.
  - Keep retry chips for true rejections/timeouts only; use calm setup copy for not-connected states.

### F5. Low-Medium — Product naming is inconsistent across auth, shell, and browser title

- Route/screen: `/login`, `/`
- Severity: Low-Medium
- Reproduction:
  1. Open `/login`.
  2. Sign in and inspect the browser tab plus top-left shell title.
- Expected: One clear product name across auth, browser title, and in-app shell.
- Actual: Auth says `Fitness Dashboard`, while the browser title and shell say `AI Coach Feed` / `AI Coach`.
- Evidence:
  - Screenshots: `/tmp/fit238-uiux-audit/01-login-desktop.png`, `/tmp/fit238-uiux-audit/04-dashboard-desktop.png`, `/tmp/fit238-uiux-audit/14-dashboard-mobile.png`
  - Code: `templates/login.html:5`
  - Code: `templates/index.html:8`, `templates/index.html:17`
- Safe FIT-238 recommendation:
  - Normalize the browser title and shell title to the chosen product name, or make the sub-brand relationship explicit.

## Untested Or Partially Tested Surfaces

Untested because they would mutate worktree-owned runtime data, require external integrations, or needed non-empty history:

- Workout completion, save, and sync-queue flows
- Active workout discard after real progress is entered
- History detail, analyze, delete, and undo paths
- Meal submission, pending-review actions, barcode flow, and photo upload
- Body measurement save path
- Settings save mutations for goals/preferences/equipment
- Export/import backup actions
- Push permission enable/disable/test flows
- Apple Health setup action, Oura sync action, and weather/service-dependent refresh paths
- Logout flow
- Delete confirmation modal in a real populated-history path

Partially tested:

- Modal system: food log and adjust-plan were keyboard-dismissable; active workout close was tested only via `Escape` and the close button, not with dirty data entered.
- Mobile layout: dashboard and settings were verified at first-run state only, not after populated history or long pending-meal lists.

## Parent-Agent Pick List

If the parent wants the safest smallest FIT-238 implementation target, choose one of these:

1. **F1 zoom unlock** — smallest global accessibility win.
2. **F2 active workout Escape fix** — smallest behavior fix with strong keyboard evidence.
3. **F3 macro header spacing fix** — smallest visible polish fix if FIT-238 needs a near-zero-risk UI cleanup.
