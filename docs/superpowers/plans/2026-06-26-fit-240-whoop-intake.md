# FIT-240 WHOOP Intake UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WHOOP data intake visible and usable from Settings with live OAuth popup/link fallback and manual paste/file import.

**Architecture:** Reuse the FIT-239 backend endpoints. Add a focused modal/sheet in `templates/index.html`, UI state and handlers in `static/js/app.js`, scoped styles in `static/css/app.css`, and contract tests in `tests/test_whoop_ui_contract.py`.

**Tech Stack:** Flask, vanilla JavaScript, existing Settings markup/CSS, pytest contract tests, Playwright browser proof.

## Global Constraints

- Branch: `codex/fit-240-whoop-intake`.
- Linear issue: `FIT-240`.
- Manual import must work when OAuth config is missing.
- Do not log, commit, screenshot, or post OAuth codes, state values, access tokens, refresh tokens, client secrets, or raw private health payloads.
- Live local server restart is the final step and must preserve `DATA_DIR=/Users/admin/fitness-dashboard`.

---

### Task 1: Settings Markup And Contract

**Files:**
- Modify: `templates/index.html`
- Modify: `tests/test_whoop_ui_contract.py`

**Interfaces:**
- Consumes: existing WHOOP Settings row IDs: `btn-connect-whoop`, `whoop-connect-state`, `whoop-detail-*`.
- Produces: modal/input IDs used by JavaScript:
  - `modal-whoop-intake`
  - `whoop-connect-modal-status`
  - `whoop-connect-open-link`
  - `whoop-import-csv-text`
  - `whoop-import-file`
  - `btn-whoop-import-file`
  - `btn-whoop-import-submit`
  - `whoop-import-status`

- [ ] Add the modal markup after the recommendation sources modal in `templates/index.html`.
- [ ] Add a visible WHOOP row affordance that opens the modal from `Connect`.
- [ ] Add test assertions that the IDs above are present.
- [ ] Run `python3 -m pytest tests/test_whoop_ui_contract.py -q`.
- [ ] Commit markup and contract tests.

### Task 2: WHOOP Intake JavaScript

**Files:**
- Modify: `static/js/app.js`
- Modify: `tests/test_whoop_ui_contract.py`

**Interfaces:**
- Consumes: `/api/whoop/connect/start`, `/api/whoop/import-csv`, `getWhoopStatus(true)`, `renderSettings()`.
- Produces:
  - `openWhoopIntakeModal()`
  - `startWhoopConnectFromModal()`
  - `importWhoopCsvFromModal()`
  - `setWhoopIntakeStatus(message, tone)`

- [ ] Make `connectWhoop()` open the intake modal instead of directly navigating.
- [ ] Implement popup/new-tab open from the fetched authorization URL and show fallback link.
- [ ] Implement paste import using JSON `{ csv }`.
- [ ] Implement file import using `FormData`.
- [ ] Refresh WHOOP status and invalidate dashboard/recommendation caches after successful import.
- [ ] Add contract tests for endpoint calls, popup fallback, import handlers, and event listeners.
- [ ] Run `node --check static/js/app.js`.
- [ ] Run `python3 -m pytest tests/test_whoop_ui_contract.py -q`.
- [ ] Commit JavaScript and tests.

### Task 3: Styling And Browser Proof Readiness

**Files:**
- Modify: `static/css/app.css`
- Modify: `tests/test_whoop_ui_contract.py`

**Interfaces:**
- Consumes: modal IDs from Task 1.
- Produces: compact desktop modal and mobile sheet layout for `.whoop-intake-*`.

- [ ] Add scoped styles for the WHOOP intake modal, textarea, file row, status text, and fallback link.
- [ ] Keep colors on existing semantic tokens/classes; no decorative gradients or glows.
- [ ] Add contract tests for scoped style selectors.
- [ ] Run `python3 -m pytest tests/test_whoop_ui_contract.py -q`.
- [ ] Run `node --check static/js/app.js`.
- [ ] Commit styles and tests.

### Task 4: Final Verification, PR, Merge, And Live Restart

**Files:**
- No planned source edits.

**Interfaces:**
- Consumes: completed branch.
- Produces: PR, PR evidence comment, Linear closeout, live server restart.

- [ ] Run focused tests: `python3 -m pytest tests/test_whoop_ui_contract.py tests/test_whoop_oauth.py tests/test_whoop_import_sync_backup.py -q`.
- [ ] Run full tests: `python3 -m pytest -q`.
- [ ] Run `node --check static/js/app.js`.
- [ ] Run `git diff --check origin/main`.
- [ ] Run artifact safety with browser proof screenshots as extra path.
- [ ] Run autoreview/codex-review until clean.
- [ ] Use browser proof on a temporary server with isolated `DATA_DIR` and `WHOOP_PROTECTED_MATERIAL_DIR`.
- [ ] Push branch, create PR, post standalone audit evidence.
- [ ] Wait for CI and require non-`DIRTY` mergeability before merge.
- [ ] Add Linear closeout comment and mark FIT-240 Done.
- [ ] Fast-forward `/Users/admin/fitness-dashboard-fit-222` to merged `main`, reload `com.fitness-dashboard`, and verify `127.0.0.1:5050` serves the new bundle while preserving `DATA_DIR=/Users/admin/fitness-dashboard`.
