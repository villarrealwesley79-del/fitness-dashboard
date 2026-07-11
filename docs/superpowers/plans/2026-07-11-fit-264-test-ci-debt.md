# FIT-264 Test and CI Debt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the frontend contracts behavior-oriented, centralize isolated Flask test bootstrap, harden local and hosted test gates, and remove midnight-dependent fixtures.

**Architecture:** Keep production behavior unchanged. Put shared Flask isolation in `tests/conftest.py`, use Node subprocess fixtures for JavaScript behavior, keep markup checks only for stable IDs and accessibility attributes, and express gate behavior as executable subprocess tests.

**Tech Stack:** Python 3.11+, pytest 8.4.2, Flask 3.1.2, Node.js 22, Bash, GitHub Actions YAML.

## Global Constraints

- No new dependencies.
- Preserve existing application behavior.
- Use `tmp_path` for every test database or data directory.
- Keep literal source assertions only for static ID, ARIA, or external configuration contracts.
- Run `python -m pytest -q` from the worktree virtual environment before handoff.

---

### Task 1: Shared isolated Flask bootstrap

**Files:**
- Modify: `tests/conftest.py`
- Modify: endpoint test modules that duplicate `SECRET_KEY`, `LOGIN_DISABLED`, database isolation, and `importlib.import_module("app")`
- Test: `tests/test_fit264_test_infrastructure.py`

**Interfaces:**
- Produces: `isolated_app` fixture returning the imported app module with `TESTING=True`, `LOGIN_DISABLED=True`, and a `tmp_path`-backed `data_store.DATA_DB`.
- Produces: `isolated_client` fixture returning `isolated_app.app.test_client()`.

- [ ] Add a failing infrastructure test proving two fixture consumers use a temporary database and no `tempfile.mkdtemp` bootstrap remains in `test_meal_intake_api.py`.
- [ ] Run `venv/bin/python -m pytest -q tests/test_fit264_test_infrastructure.py` and confirm the fixture is missing.
- [ ] Add the fixtures to `tests/conftest.py`; migrate duplicate bootstraps without changing test-specific monkeypatches.
- [ ] Run the migrated endpoint modules and confirm they pass.

### Task 2: Behavior-oriented frontend contracts

**Files:**
- Create: `tests/js_runtime.py`
- Modify: frontend contract modules returned by `rg -l 'APP_JS|APP_HTML|INDEX_HTML|STYLE_CSS' tests`
- Test: `tests/test_fit264_test_infrastructure.py`

**Interfaces:**
- Produces: `run_node_fixture(source, script)` returning parsed JSON from Node or skipping when Node is unavailable.
- Consumes: existing `_slice_between` patterns from `test_fit178_ai_coach_headline_runtime.py` and `test_fit187_active_workout_start_guard.py`.

- [ ] Add a failing audit test that rejects exact indentation-sensitive JavaScript assertions while allowing ID/ARIA presence checks.
- [ ] Run the audit test and record the offending modules.
- [ ] Convert behavior assertions to Node execution in the reported modules, retaining only stable markup/configuration presence assertions.
- [ ] Run every migrated frontend test module and the audit test.

### Task 3: Pre-push interpreter gate

**Files:**
- Modify: `.githooks/pre-push` only if live `origin/main` does not already prefer `${ROOT}/venv/bin/python`.
- Modify: `tests/test_pytest_ci_contract.py`

**Interfaces:**
- Produces: executable proof that a repo-local virtual environment wins over PATH Python.

- [ ] Add or strengthen a subprocess contract that supplies fake repo-local and PATH interpreters and asserts the repo-local interpreter is invoked.
- [ ] Run the focused test and confirm RED only if the hook lacks the required behavior.
- [ ] Keep the already-correct hook unchanged or apply the minimal interpreter selection fix.
- [ ] Run the focused hook contract.

### Task 4: Hosted CI cancellation and timeout

**Files:**
- Modify: `.github/workflows/pytest.yml`
- Modify: `tests/test_pytest_ci_contract.py`

**Interfaces:**
- Produces: workflow-level `concurrency` keyed by workflow and ref with `cancel-in-progress: true`; job-level `timeout-minutes: 10`.

- [ ] Add failing workflow contract assertions for concurrency and timeout.
- [ ] Run `venv/bin/python -m pytest -q tests/test_pytest_ci_contract.py` and confirm RED.
- [ ] Add the minimal YAML keys.
- [ ] Rerun the workflow contract and confirm GREEN.

### Task 5: Midnight-stable date fixtures

**Files:**
- Modify: `tests/test_food_logs_by_date.py`
- Modify: `tests/test_nutrition_history_breakdown.py`
- Modify: `tests/test_meal_intake_api.py`
- Test: `tests/test_fit264_test_infrastructure.py`

**Interfaces:**
- Produces: fixed test-local reference dates or monkeypatched application clock values used throughout each test.

- [ ] Add an audit assertion rejecting direct `datetime.now()` calls in the three named test modules.
- [ ] Run it and confirm RED.
- [ ] Replace import-time/current-time helpers with fixed dates and patch the application date source where endpoint behavior depends on now.
- [ ] Run the three modules and audit test.

### Task 6: Full verification and handoff

**Files:**
- Review all changed files; create no additional production files.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: one FIT-264 commit, Draft PR, review audit, and exact-head factory-review request.

- [ ] Run `git diff --check` and inspect the scope.
- [ ] Run `venv/bin/python -m pytest -q` and require a clean full-suite result.
- [ ] Run `/Users/admin/.codex/skills/autoreview/scripts/autoreview --mode local` until no accepted/actionable findings remain.
- [ ] Commit, push without bypassing hooks, create the Draft PR, post audit evidence, update Linear to `built`, and request exact-head `factory-review`.
