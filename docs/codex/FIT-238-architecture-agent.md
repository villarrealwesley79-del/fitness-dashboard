# FIT-238 Architecture / Code Discovery

Date: 2026-06-25
Worktree: `/Users/admin/codex-worktrees/fitness-fit-238-qol`
Branch: `villarrealwesley79/fit-238-one-time-quality-of-life-pass-for-core-fitness-dashboard`

Scope: read-only discovery for small, safe quality-of-life fixes. Product code was not edited.

## Inputs Reviewed

- `docs/superpowers/plans/2026-06-25-fit-238-quality-of-life-pass.md`
- `README.md`
- `/Users/admin/.codex/ui/DESIGN.md` as fallback UI contract
- Code graph project `Users-admin-codex-worktrees-fitness-fit-238-qol`
- `app.py`
- `templates/index.html`, plus public templates at `templates/login.html`, `templates/landing.html`, `templates/pricing.html`
- `static/js/app.js`, `static/css/style.css`, `static/js/sw.js`
- Focused static/contract tests under `tests/`

## Current Surface Map

### Server

- `app.py` is the main Flask surface. It is large, route-heavy, and mixes dashboard API, meal intake, workout, Oura, Apple Health, push, and history endpoints in one file.
- Core page routes:
  - `app.py:4454` `/` -> `index()` renders `templates/index.html` with `Cache-Control: no-store`.
  - `app.py:4715` `/gym-now` -> `gym_now()` renders an inline emergency workout page for stale mobile/PWA caches.
- Core dashboard/data routes:
  - `app.py:4782` `/api/dashboard` -> `api_dashboard()`
  - `app.py:5010` `/api/vitals`
  - `app.py:11459` `/api/recommendation/smart`
  - `app.py:11087` `/api/freshness`
- Nutrition/meal routes:
  - `app.py:7135` `/api/meal-intake`
  - `app.py:7379` `/api/meal-intake/barcode`
  - `app.py:7490` `/api/meal-intake/pending`
  - `app.py:7553` `/api/meal-intake/<meal_id>/refresh`
  - `app.py:7862` `/api/nutrition-today`
  - `app.py:7961` `/api/nutrition-history`
  - `app.py:8040` `/api/food-logs/by-date/<date>`
- Workout routes:
  - `app.py:4670` `/api/next-workout`
  - `app.py:5201` `/api/add-workout`
  - `app.py:8689` `/api/workout/swap`
  - `app.py:9206` `/api/workout/adjust`
  - `app.py:9576` `/api/workout/analyze`
  - `app.py:12007` `/api/complete-workout`

### Templates

- `templates/index.html` is the main authenticated app shell. It contains all primary tabs, modal shells, food logging UI, workout UI, settings, and sync queue UI.
- Main tab structure:
  - Dashboard: `#tab-dashboard`
  - Vitals: `#tab-vitals`
  - Next workout: `#tab-workout`
  - Log: `#tab-log`
  - History: `#tab-history`
  - Body: `#tab-body`
  - Stats: `#tab-stats`
  - Settings: `#tab-settings`
- Modal shells in `templates/index.html`:
  - `#modal-swap`
  - `#modal-adjust`
  - `#modal-analyze`
  - `#modal-workout-detail`
  - `#modal-meal-detail`
  - `#modal-food-log`
  - `#modal-delete-confirm`
  - `#modal-apple`
  - `#modal-active`
  - `#modal-workout-saved`
  - `#modal-sync-queue`
- Public templates are isolated and small:
  - `templates/login.html`
  - `templates/landing.html`
  - `templates/pricing.html`

### Static JavaScript

- `static/js/app.js` is the main browser runtime.
- Important existing patterns:
  - One global `state` object at the top of the file.
  - `api(path, opts)` helper centralizes fetch behavior and CSRF header handling.
  - `switchTab()` updates active classes, `aria-hidden`, `aria-selected`, and roving `tabIndex`.
  - `handleTabKeydown()` supports ArrowLeft, ArrowRight, Home, and End on tab buttons.
  - `focusOpenModal()`, `restoreModalFocus()`, `closeModal()`, `getTopmostOpenModal()`, `handleModalEscape()`, and `watchModalFocus()` are the existing modal focus/escape primitives.
  - Dashboard fetches are guarded by generation counters and per-card retry chips.
  - Browser-only surfaces are tested through source-contract tests because the repo does not appear to have a JS unit runner.
- High-value regions:
  - Modal/focus helpers: `static/js/app.js:900-980`
  - Dashboard render: `static/js/app.js:1431`
  - Dashboard painter: `static/js/app.js:1493`
  - Active workout render: `static/js/app.js:5588`
  - Sync queue: `static/js/app.js:5942`, `static/js/app.js:6339`, `static/js/app.js:6460`
  - Meal pending list: `static/js/app.js:7971`
  - Barcode submit: `static/js/app.js:8971`
  - Meal submit: `static/js/app.js:9067`

### Static CSS

- `static/css/style.css` is the main design surface.
- Important existing patterns:
  - Existing custom tokens use names such as `--fg-0`, `--fg-1`, `--fg-dim`, `--accent-blue`, `--border-2`.
  - Focus treatment exists for many special controls.
  - Modal stack ordering is already issue-documented: `#modal-swap`, `#modal-meal-detail`, `#modal-delete-confirm`, `#modal-adjust`, `#modal-sync-queue`.
  - `.btn-secondary` has an existing dark-theme readability regression test.
- High-value regions:
  - Tab bar: `static/css/style.css:215`
  - Retry chips: `static/css/style.css:684`
  - Buttons: `static/css/style.css:1012`
  - Modal shell: `static/css/style.css:1598`
  - Active workout modal: `static/css/style.css:2805`
  - Empty states: `static/css/style.css:2875`
  - Meal detail / body nutrition: `static/css/style.css:3021`
  - Meal composer: `static/css/style.css:3551`
  - Meal pending review: `static/css/style.css:3756`
  - Meal review v2: `static/css/style.css:4027`

### Focused Tests Already Available

- `tests/test_fit192_accessibility_contract.py`
  - Tab ARIA contract
  - Tab keyboard behavior source contract
  - Modal escape/focus helper source contract
  - Current JS/SW cache-bust version contract
- `tests/test_button_styles_contract.py`
  - `.btn-secondary` readability
  - Meal detail Correct button class contract
- `tests/test_dashboard_render_contract.py`
  - Dashboard painter guard/reset contracts
  - Stats empty-state reset
- `tests/test_dashboard_retry_contract.py`
  - Retry chips in template/CSS/JS
  - Per-slice dashboard fetch fan-out
  - Timeout/retry generation guard behavior
- `tests/test_fit145_offline_queue.py`
  - Offline meal queue static JS/CSS/HTML/privacy contracts
- `tests/test_fit150_pending_refresh.py`
  - Meal review v2 disabled-state contracts during pending refresh
- `tests/test_fit219_review_card_a11y.py`
  - Meal review card focus/expand/confidence ARIA contracts
- `tests/test_fit139_refresh_ui.py`, `tests/test_fit137_adaptation_ui.py`, `tests/test_fit177_ai_coach_headline_announcement.py`
  - Smaller source contracts around live regions and UI notices.

## Existing Patterns To Preserve

- Keep FIT changes narrow and issue-local. This repo already uses issue-number comments to explain why a UI guard exists.
- Prefer source-contract pytest tests for static HTML/CSS/JS changes unless a real JS/browser test harness already exists for the exact surface.
- Do not rewrite the Flask/static/templates structure. The FIT-238 plan explicitly says to preserve existing data contracts.
- For CSS, use existing variables and component classes before adding new tokens.
- For JavaScript, use existing helpers (`$`, `qsa`, `api`, `focusOpenModal`, `closeModal`) rather than adding parallel abstractions.
- If `static/js/app.js` or `static/js/sw.js` changes, expect cache-bust contracts in `tests/test_fit192_accessibility_contract.py` to need intentional updates. If only `templates/index.html` changes, the existing app route is already no-store.

## Low-Risk Implementation Candidates

### Candidate A: Add dialog semantics to existing modal shells

Recommended first scoped fix.

Problem observed:
- `templates/index.html` has many `.modal` containers with stable modal structure and JS focus handling, but no `role="dialog"` or `aria-modal="true"` attributes were found.
- Several modal titles also lack ids, which prevents clean `aria-labelledby` wiring.

Why this is low risk:
- It is mostly static template markup.
- It does not change data contracts, fetch behavior, modal visibility, z-index, or form submission.
- Existing JS already treats `.modal` containers as the focus/escape boundary.
- Existing `tests/test_fit192_accessibility_contract.py` already has an HTML parser helper and is the natural home for a modal semantics contract.

Likely files:
- `templates/index.html`
- `tests/test_fit192_accessibility_contract.py`

Suggested implementation shape:
- For each `.modal` container, add:
  - `role="dialog"`
  - `aria-modal="true"`
  - `aria-labelledby="<title-id>"`
- Give every modal title a stable id where missing:
  - `adjust-modal-title`
  - `food-log-modal-title`
  - `delete-confirm-modal-title`
  - `apple-modal-title`
  - `workout-saved-modal-title`
  - `sync-queue-modal-title`
- Reuse existing ids where present:
  - `swap-modal-title`
  - `analyze-title`
  - `workout-detail-title`
  - `meal-detail-title`
  - `active-workout-title`
- Optional same-scope polish: change modal close `aria-label="close"` to `aria-label="Close"` for consistency.

Focused tests:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py -q
```

Useful test addition:
- Add `test_modal_markup_has_dialog_semantics()` to `tests/test_fit192_accessibility_contract.py`.
- Assert every `.modal` has `role="dialog"`, `aria-modal="true"`, and an `aria-labelledby` pointing to an existing heading id.
- Assert every `.modal-close` has a non-empty `aria-label`.

Browser validation:
- Start the app and open at least these modal paths on desktop and mobile viewport:
  - Adjust Plan
  - Food log
  - Meal details from a food row if available
  - Active workout
  - Pending sync when queue state exists, or confirm the hidden shell still has correct semantics
- Keyboard checks:
  - Focus lands inside the opened modal.
  - Escape closes non-active modals.
  - Close button is reachable by Tab.
  - Active workout modal still obeys existing guarded close behavior.

### Candidate B: Add visible keyboard focus styling for modal close buttons

Problem observed:
- `.modal-close` has hover styling but no explicit `:focus-visible` rule in `static/css/style.css`.
- The fallback design contract requires visible focus rings.

Why this is low risk:
- CSS-only.
- Does not affect data flow.
- The close buttons already exist in every modal and are the first modal focus target through `MODAL_FOCUS_SELECTOR`.

Likely files:
- `static/css/style.css`
- `tests/test_fit192_accessibility_contract.py` or `tests/test_button_styles_contract.py`
- Possibly `templates/index.html` only if combined with Candidate A.

Suggested implementation shape:
- Add a rule near `.modal-close:hover`:

```css
.modal-close:focus-visible {
    outline: 2px solid var(--accent-blue);
    outline-offset: 2px;
    background: rgba(51,65,85,0.5);
    color: var(--fg-0);
}
```

Focused tests:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py tests/test_button_styles_contract.py -q
```

Useful test addition:
- Assert `.modal-close:focus-visible` exists and uses `outline` or `box-shadow`.

Browser validation:
- Open any modal, press Tab until close button is focused, confirm visible ring on desktop and mobile emulation.

### Candidate C: Normalize modal title ids and close labels only

Problem observed:
- Some modal headings have ids and some do not.
- Close buttons all use lowercase `aria-label="close"`.

Why this is low risk:
- Static HTML-only if not adding full dialog semantics.
- Can be verified with the same parser test.

Likely files:
- `templates/index.html`
- `tests/test_fit192_accessibility_contract.py`

Focused tests:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py -q
```

Note:
- This is less valuable than Candidate A because ids matter most when connected through `aria-labelledby`.

### Candidate D: Tighten one stale/empty copy mismatch

Problem observed:
- The food-log empty state says `Use Log a Meal to add your first entry.`, while the visible app has a `Log` tab and an inline meal composer rather than a clearly named `Log a Meal` command.

Why this is low risk:
- Static copy-only.
- No data or styling impact.

Likely files:
- `templates/index.html`
- Optional static contract test in a small UI/copy test if the parent wants this frozen.

Focused tests:

```bash
python -m pytest tests/test_dashboard_render_contract.py -q
```

Browser validation:
- Open Food log with no meals available and confirm the empty hint matches the visible UI.

Note:
- This is safe but lower value than modal semantics because it needs real empty-state browser evidence to confirm the mismatch matters.

## Risky Candidates To Defer

- Broad dashboard visual redesign. The CSS and JS are dense, issue-layered, and already carry many regression contracts. FIT-238 explicitly bans broad redesign.
- Reworking the tab bar icons from text glyphs to a new icon system. This would touch navigation visual language and likely needs broader visual QA.
- Refactoring `static/js/app.js` into modules. The file is large, but FIT-238 is a QOL pass, not an architecture migration.
- Changing `api_dashboard()` behavior or nutrition/workout payload shape. Many tests and UI painters depend on existing response fields.
- Reworking `/gym-now` inline HTML/CSS. It is visually separate and has inline styling that does not fully match the main app, but it is an emergency stale-cache escape hatch. Any changes should have a dedicated route/template test and live mobile proof.
- Changing service worker/cache behavior. Existing tests pin the service worker version and offline behavior; this can create stale-client regressions if bundled into a QOL pass.
- Large accessibility work on all dynamic meal review controls. There are already targeted FIT-150/FIT-219 contracts; expand only with confirmed barriers.
- Changing private/runtime paths, auth databases, local JSON data, health exports, logs, or generated artifacts. README and FIT-238 plan both say these stay out of scope.

## Focused Verification Commands

For Candidate A:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py -q
```

For Candidate B:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py tests/test_button_styles_contract.py -q
```

For any `static/js/app.js` edit:

```bash
node --check static/js/app.js
python -m pytest tests/test_fit192_accessibility_contract.py tests/test_dashboard_retry_contract.py tests/test_dashboard_render_contract.py -q
```

For meal review / pending refresh UI edits:

```bash
python -m pytest tests/test_fit150_pending_refresh.py tests/test_fit219_review_card_a11y.py tests/test_meal_intake_review_contract.py -q
```

For offline sync queue UI edits:

```bash
python -m pytest tests/test_fit145_offline_queue.py tests/test_workout_sync_queue_js.py tests/test_offline_workout_sync.py -q
```

For CSS/template-only safety:

```bash
python -m pytest tests/test_button_styles_contract.py tests/test_fit192_accessibility_contract.py tests/test_dashboard_retry_contract.py -q
git diff --check
```

Known baseline caveat from the FIT-238 plan:
- The full suite currently has one pre-existing baseline failure:
  `tests/test_fit136_workout_adaptation.py::test_guardrail_metadata_has_required_citations_and_neutral_language`
  caused by random UUID text containing banned substring `bad`.

## Recommended Parent Implementation

Implement Candidate A first. It is the most scoped, highest-confidence quality-of-life fix from this discovery pass:

1. Update modal containers and title ids in `templates/index.html`.
2. Add `test_modal_markup_has_dialog_semantics()` in `tests/test_fit192_accessibility_contract.py`.
3. Run:

```bash
python -m pytest tests/test_fit192_accessibility_contract.py -q
git diff --check
```

4. Browser-check Adjust Plan, Food log, Active workout, and one detail/delete modal on desktop and mobile. Confirm focus lands inside, Escape behavior still matches the existing contract, and the close button remains reachable.

If there is room for one adjacent static polish item, combine Candidate B only if the parent can do browser keyboard proof. Otherwise keep Candidate B as a follow-up.
