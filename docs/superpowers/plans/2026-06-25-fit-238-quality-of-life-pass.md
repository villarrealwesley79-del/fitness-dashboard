# FIT-238 Quality-of-Life Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the Fitness Dashboard core experience through a one-time, evidence-backed UI/UX, accessibility, and product-surface quality pass.

**Architecture:** Audit first, then implement only the smallest coherent fixes supported by real browser evidence. Keep UI changes in the existing Flask/static/templates structure and preserve current data contracts.

**Tech Stack:** Python 3, Flask 3.1.2, Flask-Login 0.6.3, pytest 8.4.2, static HTML/CSS/JavaScript.

## Global Constraints

- Linear issue: `FIT-238`.
- Branch: `villarrealwesley79/fit-238-one-time-quality-of-life-pass-for-core-fitness-dashboard`.
- Worktree: `/Users/admin/codex-worktrees/fitness-fit-238-qol`.
- No recurring/nightly automation.
- No production data changes.
- No broad visual redesign.
- No unrelated refactors.
- No destructive cleanup of uncertain local artifacts.
- Follow `/Users/admin/.codex/ui/DESIGN.md` as fallback design contract when the repo has no stronger local design system.
- Preserve private runtime files such as `.env`, auth databases, SQLite databases, local health exports, runtime JSON data, logs, backups, and virtualenvs.
- Initial baseline full test run hit a flaky failure in `tests/test_fit136_workout_adaptation.py::test_guardrail_metadata_has_required_citations_and_neutral_language`, caused by random UUID text containing banned substring `bad`; verification rerun later showed the exact test, file, and full suite passing, so do not special-case it unless it reproduces again.

---

### Task 1: Parallel Discovery And Evidence

**Files:**
- Modify: `.superpowers/sdd/progress.md`
- Create: `docs/codex/FIT-238-agent-findings.md`

**Interfaces:**
- Consumes: FIT-238 Linear issue and this plan.
- Produces: A ranked evidence list with confirmed UI/UX, accessibility, product-surface, architecture/code, and verification findings.

- [ ] **Step 1: Dispatch read-only UI/UX agent**

Ask the agent to run the UI/UX Score Loop against the core daily flow, starting from fresh browser state where possible. Required deliverable: ranked findings with route/screen, reproduction steps, expected/actual, screenshot paths if captured, and whether the finding is safe to implement in FIT-238.

- [ ] **Step 2: Dispatch read-only accessibility agent**

Ask the agent to check keyboard navigation, focus visibility, contrast, zoom/responsive behavior, form labels, and modal/sheet behavior where available. Required deliverable: confirmed barriers only, ranked by user harm, with exact reproduction steps.

- [ ] **Step 3: Dispatch read-only product-surface agent**

Ask the agent to inventory routes, buttons, forms, modals, details panels, empty states, warning states, and mobile views that are practical to validate locally. Required deliverable: coverage map and untested surfaces.

- [ ] **Step 4: Dispatch read-only architecture/code agent**

Ask the agent to inspect existing static/template/app patterns for likely small fixes, without editing. Required deliverable: file map, safe change candidates, and risky candidates to defer.

- [ ] **Step 5: Dispatch read-only verification agent**

Ask the agent to identify the narrowest useful tests and browser validation steps for likely UI/static/template changes. Required deliverable: commands, expected pass criteria, and known baseline caveats.

- [ ] **Step 6: Synthesize findings**

Write `docs/codex/FIT-238-agent-findings.md` with confirmed findings, rejected/non-actionable findings, chosen implementation target, deferred follow-ups, and verification plan.

### Task 2: Implement Highest-Value Scoped Fixes

**Files:**
- Modify only files justified by `docs/codex/FIT-238-agent-findings.md`.
- Test: add or update focused tests only when changed behavior is testable without brittle screenshots.

**Interfaces:**
- Consumes: `docs/codex/FIT-238-agent-findings.md`.
- Produces: Minimal quality-of-life fix set with focused tests.

- [ ] **Step 1: Select one coherent fix set**

Choose the smallest implementation that addresses the highest-impact confirmed finding without broad redesign or unrelated refactor.

- [ ] **Step 2: Add or update focused tests first where practical**

Use pytest for backend/template/static contract behavior when possible. If browser-only visual behavior cannot be meaningfully asserted in pytest, document it in the browser validation plan instead of adding brittle tests.

- [ ] **Step 3: Implement the fix**

Edit only the files needed for the selected fix set. Preserve existing naming, structure, routes, and state contracts.

- [ ] **Step 4: Run focused tests**

Run the narrowest relevant pytest commands for touched behavior. If JavaScript changes are made, run a syntax check where possible.

### Task 3: End-To-End Validation

**Files:**
- Modify: `docs/codex/FIT-238-validation.md`

**Interfaces:**
- Consumes: implemented diff.
- Produces: validation evidence for PR and Linear closeout.

- [ ] **Step 1: Start the local app**

Run the Flask app from the FIT-238 worktree on an available local port.

- [ ] **Step 2: Validate with real browser interaction**

Use real browser/computer interaction for the changed flow. Include clicks, keyboard navigation, desktop and mobile viewport checks, and relevant empty/error/blocked states where practical.

- [ ] **Step 3: Record results**

Write `docs/codex/FIT-238-validation.md` with commands, browser steps, screenshots or notes, pass/fail outcome, and known baseline caveats.

### Task 4: Review And Publish

**Files:**
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: final diff and validation evidence.
- Produces: commit, pushed branch, PR evidence, and Linear closeout comment when publishing is appropriate.

- [ ] **Step 1: Review diff**

Run `git diff --check`, inspect the complete diff for unrelated changes, and run an independent review.

- [ ] **Step 2: Fix accepted review findings**

Fix any accepted/actionable review findings and rerun focused tests plus validation.

- [ ] **Step 3: Commit and push**

Commit only FIT-238 files, push the branch, open or update the GitHub PR, and post review/test evidence.

- [ ] **Step 4: Close out Linear**

Add a FIT-238 comment with branch, PR, commit, tests, browser validation, review result, mergeability, blockers, and follow-ups.
