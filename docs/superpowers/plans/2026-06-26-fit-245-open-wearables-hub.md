# FIT-245 Open Wearables Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Open Wearables the primary wearable integration hub while keeping Fitness Dashboard responsible for coaching decisions, normalized history, recommendation safety, source proof, and AI approval boundaries.

**Architecture:** Open Wearables remains a separate secured service. Fitness Dashboard adds a thin adapter, a normalized fact store, canonical training categories, recommendation source projection, and sanitized AI fact access. Existing direct Apple Health, Oura, and WHOOP behavior remains available as fallback and compatibility surface.

**Tech Stack:** Python 3, Flask, SQLite, pytest, plain JavaScript in `static/js/app.js`, existing templates/CSS, Linear, GitHub, browser QA with desktop/mobile and light/dark proof.

## Global Constraints

- Linear spec issue: `FIT-245`.
- Current spec branch: `codex/fit-245-open-wearables-hub-spec`.
- Current spec worktree: `/Users/admin/codex-worktrees/fitness-fit-245-open-wearables-hub-spec`.
- Before app code changes, create a new code-facing Linear issue or issue set from this plan and start a fresh branch/worktree from latest `origin/main`.
- Do not edit `/Users/admin/fitness-dashboard` directly except read-only inspection.
- Do not replace or supervise the Open Wearables Docker/Postgres/Redis/Celery runtime from Fitness Dashboard in the first implementation slice.
- Keep Open Wearables credentials, provider tokens, API keys, raw provider payloads, raw Open Wearables payloads, auth headers, logs, local DBs, health exports, screenshots with private values, and generated runtime files out of git.
- Preserve the existing redacted `/api/health/sync` contract; raw Open Wearables/provider payloads must not appear in user-facing API responses, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- Remote Open Wearables hosts require TLS and host allowlisting. Localhost remains the safe default for development.
- Model all Open Wearables-supported providers generically. The first implementation can prove with available providers, but the UI and storage model must not hardcode WHOOP/Oura/Apple as the only supported provider family.
- Store Fitness Dashboard wearable data as normalized daily facts and provider/source metadata only.
- Keep direct Apple Health, Oura, and WHOOP fallbacks until replacement parity is explicitly proven.
- Treat manual logs, injury/soreness notes, and completed workout history as higher authority than wearable optimism.
- Wearable data may reduce confidence, lower intensity, or add caution; it must not push the plan harder when sources conflict.
- Map local `Lifted`, Apple Watch `Functional Strength Training`, Apple Watch `Traditional Strength Training`, and equivalent provider labels into canonical `strength_training` for History, Stats, recommendations, and AI answers.
- Preserve original source labels, source ids, dates, and provenance beside canonical categories.
- AI can answer over sanitized facts and reconciled history. AI cannot silently mutate records, mappings, or plans; every suggestion that changes state requires explicit user approval.
- UI work must follow the existing app style first and `/Users/admin/.codex/ui/DESIGN.md` only as fallback.
- Before merge, run a systematic UI audit covering desktop/mobile, light/dark, Settings hub/provider states, setup/check states, History grouping, Stats totals, recommendation source proof, AI query/suggestion approval, mobile nav overlap, empty states, warning states, and blocked/error states.
- If a check cannot run because Open Wearables credentials or live provider data are missing, create deterministic mocked or fixture-backed proof and state the real-data blocker plainly.

---

### Task 0: Execution Issue And Worktree Setup

**Owner:** main controller

**Files:**
- Read: `docs/superpowers/specs/2026-06-26-fit-245-open-wearables-hub-design.md`
- Read: `docs/superpowers/plans/2026-06-26-fit-245-open-wearables-hub.md`
- Create: Linear implementation issue or issue set derived from this plan.
- Create: fresh code worktree from latest `origin/main`.

**Interfaces:**
- Produces: implementation Linear issue id, code branch, clean worktree, and scoped acceptance criteria.

- [ ] Pull or fetch latest `origin/main`.
- [ ] Create one implementation issue for the first full slice, or split into these issues if scope pressure is high:
  - Open Wearables hub setup and provider status UI.
  - Normalized wearable fact store and sanitized sync/status projection.
  - Canonical training categories for History/Stats.
  - Recommendation source proof and conservative conflict handling.
  - AI fact query layer and approval-required suggestions.
  - Systematic UI audit and fix pass.
- [ ] Create a fresh branch/worktree for the implementation issue.
- [ ] Confirm `git status --short --branch` is clean before code edits.
- [ ] Confirm no unrelated local work is present in the implementation worktree.

Expected setup proof:

```bash
git fetch origin
git status --short --branch
```

### Task 1: Open Wearables Adapter And Sanitized Hub Routes

**Owner:** backend integration agent

**Files:**
- Create: `open_wearables_adapter.py`
- Modify: `app.py`
- Test: `tests/test_open_wearables_adapter.py`
- Extend: `tests/test_open_wearables_health_sync_redaction.py`

**Interfaces:**
- Produces: `OpenWearablesAdapter`, `OpenWearablesStatus`, `OpenWearablesProviderStatus`, `redact_open_wearables_error()`.
- Extends routes:
  - `GET /api/open-wearables/status`
  - `POST /api/open-wearables/setup/check`
  - `POST /api/open-wearables/sync`
  - `GET /api/open-wearables/providers`
- Preserves: existing `/api/health/sync` redaction behavior.

- [ ] Write failing tests for missing config, localhost config, disallowed remote host, non-TLS remote host, allowed TLS remote host, auth reference redaction, service unavailable, timeout, malformed provider payload, provider capability gaps, and sanitized sync metadata.
- [ ] Implement adapter config loading without exposing secrets.
- [ ] Move or wrap existing Open Wearables fetch logic behind the adapter without broad behavior changes.
- [ ] Add provider capability normalization with optional fields for metrics, workouts, history, webhooks, sync support, last sync, stale state, and error state.
- [ ] Ensure all route responses include status/freshness/capability metadata only.
- [ ] Ensure error responses expose stable error codes and safe summaries only.
- [ ] Run focused tests.

Reference shape:

```python
@dataclass(frozen=True)
class OpenWearablesProviderStatus:
    provider_id: str
    label: str
    state: str
    capabilities: dict[str, bool]
    last_sync_at: str | None = None
    stale: bool = False
    error_code: str | None = None
```

Verification:

```bash
python3 -m pytest tests/test_open_wearables_adapter.py tests/test_open_wearables_health_sync_redaction.py -q
```

### Task 2: Normalized Wearable Fact Store

**Owner:** backend data agent

**Files:**
- Create: `wearable_fact_store.py`
- Modify: `app.py`
- Test: `tests/test_wearable_fact_store.py`
- Extend: `tests/test_wearable_freshness_contract.py`
- Extend: `tests/test_freshness.py`

**Interfaces:**
- Produces: `init_wearable_fact_db()`, `upsert_daily_facts()`, `list_wearable_sources()`, `latest_wearable_freshness()`, `list_recommendation_facts()`.
- Extends:
  - `GET /api/wearable-sources`
  - `GET /api/wearable-facts`
  - `_compute_data_freshness()`
  - `_wearable_sources_payload()`

- [ ] Write tests for schema creation, idempotent daily fact upsert, provider metadata upsert, source freshness, stale status, conflict state, missing data state, capability state, recommendation-use marker, and redaction of raw payload-like keys.
- [ ] Store only normalized facts such as sleep duration, sleep quality band, resting heart rate, HRV band, strain/load band, recovery/readiness band, workout event summary, source, confidence, and freshness.
- [ ] Add provider/source rows that can represent all Open Wearables-supported providers without hardcoded provider-specific UI assumptions.
- [ ] Integrate fact freshness into dashboard/freshness payloads without removing current Oura/Apple/WHOOP fallback rows.
- [ ] Add defensive validation to reject raw payload fields, token-looking fields, auth headers, or nested provider payload blobs.
- [ ] Run focused tests plus existing freshness contracts.

Reference shape:

```python
@dataclass(frozen=True)
class WearableDailyFact:
    date: str
    provider_id: str
    source_label: str
    metric: str
    value: float | str | None
    unit: str | None
    band: str | None
    confidence: str
    freshness: str
    conflict_state: str | None = None
```

Verification:

```bash
python3 -m pytest tests/test_wearable_fact_store.py tests/test_wearable_freshness_contract.py tests/test_freshness.py -q
```

### Task 3: Canonical Training History And Stats

**Owner:** history/data agent

**Files:**
- Create: `history_normalization.py`
- Modify: `app.py`
- Modify: `apple_health_parser.py`
- Modify: `health_ingest.py`
- Modify: `static/js/app.js`
- Test: `tests/test_history_canonical_categories.py`
- Extend: `tests/test_apple_health_hae_dates.py`
- Extend: `tests/test_apple_health_recommendation_bridge.py`
- Extend: `tests/test_history_detail_and_analyze.py`
- Extend: `tests/test_dashboard_render_contract.py`

**Interfaces:**
- Produces: `canonical_training_category()`, `history_source_label()`, `normalize_history_item()`.
- Extends:
  - `/api/history`
  - `/api/history-all`
  - Apple Health workout normalization
  - History tab filters
  - Stats workout category totals

- [ ] Write tests proving `Lifted`, `Functional Strength Training`, `Traditional Strength Training`, and provider strength labels map to `strength_training`.
- [ ] Write tests proving original labels, source labels, source ids, exercise detail, workout dates, and duration remain visible.
- [ ] Write tests preventing date-only dedupe from hiding legitimate two-a-day workouts.
- [ ] Add canonical category fields to local workout history responses.
- [ ] Add canonical category and original label fields to Apple Health workout import/normalization.
- [ ] Update History filters to group by canonical category by default while preserving source-specific filters where useful.
- [ ] Update Stats to include provider/watch strength sessions in category-level totals without double-counting local lifted records.
- [ ] Run focused tests.

Reference shape:

```python
def canonical_training_category(label: str | None, source: str | None = None) -> str:
    normalized = " ".join((label or "").lower().split())
    if normalized in {"lifted", "functional strength training", "traditional strength training"}:
        return "strength_training"
    if "strength" in normalized or normalized in {"weight training", "resistance training"}:
        return "strength_training"
    return normalized.replace(" ", "_") or "unknown"
```

Verification:

```bash
python3 -m pytest tests/test_history_canonical_categories.py tests/test_apple_health_hae_dates.py tests/test_apple_health_recommendation_bridge.py tests/test_history_detail_and_analyze.py tests/test_dashboard_render_contract.py -q
```

### Task 4: Recommendation Source Proof And Conflict Policy

**Owner:** recommendation safety agent

**Files:**
- Create: `recommendation_sources.py`
- Modify: `app.py`
- Modify: `whoop_recommendations.py`
- Modify: `static/js/app.js`
- Test: `tests/test_recommendation_sources.py`
- Extend: `tests/test_whoop_recommendations.py`
- Extend: `tests/test_whoop_source_conflicts.py`
- Extend: `tests/test_apple_health_recommendation_bridge.py`
- Extend: `tests/test_progress_loop_completion_to_recommendation.py`

**Interfaces:**
- Produces: `build_recommendation_source_proof()`, `apply_wearable_fact_modifiers()`, `detect_source_conflicts()`.
- Extends:
  - `/api/recommendation/smart`
  - `/api/next-workout`
  - dashboard recommendation payloads
  - frontend recommendation source summary/drawer.

- [ ] Write tests for fresh Open Wearables sleep/recovery caution, stale facts as display-only, source conflict conservative choice, missing provider no-op, Apple Health/manual history precedence, and no plan-hardening from optimistic wearable data.
- [ ] Convert WHOOP-specific source proof into a generic recommendation source projection while preserving current WHOOP behavior.
- [ ] Include source/freshness/conflict/evidence in recommendation payloads without raw payload leakage.
- [ ] Ensure end-user proof explains why a plan changed when Open Wearables-derived facts reduced intensity or confidence.
- [ ] Keep conflict states visible in UI and lower confidence instead of hiding disagreements.
- [ ] Run focused recommendation tests.

Reference shape:

```python
@dataclass(frozen=True)
class RecommendationSourceProof:
    source_id: str
    label: str
    provider_id: str | None
    freshness: str
    influence: str
    reason: str
    conflict_state: str | None = None
```

Verification:

```bash
python3 -m pytest tests/test_recommendation_sources.py tests/test_whoop_recommendations.py tests/test_whoop_source_conflicts.py tests/test_apple_health_recommendation_bridge.py tests/test_progress_loop_completion_to_recommendation.py -q
```

### Task 5: Sanitized AI Fact Query And Approval Boundary

**Owner:** AI workflow agent

**Files:**
- Create: `ai_fact_query.py`
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `static/js/app.js`
- Modify: `static/css/app.css`
- Test: `tests/test_ai_fact_context.py`
- Extend: `tests/test_dashboard_render_contract.py`

**Interfaces:**
- Produces: `build_ai_fact_context()`, `answer_fact_question()`, `create_pending_ai_suggestion()`, `approve_ai_suggestion()`.
- Adds routes:
  - `GET /api/ai/facts/context`
  - `POST /api/ai/facts/query`
  - `POST /api/ai/suggestions/<suggestion_id>/approve`
  - `POST /api/ai/suggestions/<suggestion_id>/reject`
- Does not reuse mutating workout adjustment routes for automatic AI changes.

- [ ] Write tests proving AI context excludes raw payloads, token-like fields, auth headers, provider payload blobs, and private config.
- [ ] Write tests proving AI answers include date range, source, freshness, and uncertainty when evidence is stale or incomplete.
- [ ] Write tests proving suggested mutations are pending by default and require explicit approval before state changes.
- [ ] Implement a deterministic local fact-query path for questions about training history, wearable freshness, source conflicts, and recommendation evidence.
- [ ] Add a minimal UI for asking fact questions and reviewing pending suggestions without implying automatic mutation.
- [ ] Run focused tests.

Reference shape:

```python
@dataclass(frozen=True)
class AiFactAnswer:
    answer: str
    evidence: list[dict[str, str]]
    uncertainty: str | None
    suggested_action_id: str | None = None
```

Verification:

```bash
python3 -m pytest tests/test_ai_fact_context.py tests/test_dashboard_render_contract.py -q
```

### Task 6: Settings Hub, Provider Rows, And Frontend Wiring

**Owner:** frontend agent

**Files:**
- Modify: `templates/index.html`
- Modify: `static/js/app.js`
- Modify: `static/css/app.css`
- Test: `tests/test_open_wearables_ui_contract.py`
- Extend: `tests/test_dashboard_render_contract.py`

**Interfaces:**
- Consumes:
  - `/api/open-wearables/status`
  - `/api/open-wearables/setup/check`
  - `/api/open-wearables/sync`
  - `/api/open-wearables/providers`
  - `/api/wearable-sources`
  - recommendation source payloads
  - AI fact query payloads.
- Produces: Settings Open Wearables hub row, provider child rows, setup/check action, sync action, provider detail state, source proof presentation, and grouped History/Stats presentation.

- [ ] Write render contract tests for hub missing config, localhost ready, remote blocked, service down, connected, no providers, provider stale, provider error, provider capability gap, sync running, sync complete, and redacted errors.
- [ ] Add Settings hub row with service health, auth/config state, last successful sync, and setup/check action.
- [ ] Add provider child rows that render from provider metadata and capabilities, not hardcoded provider names.
- [ ] Preserve existing Oura/Apple/WHOOP rows until their replacement path is proven.
- [ ] Update recommendation source summary/drawer to show Open Wearables-derived source proof and conflict states.
- [ ] Update History and Stats UI to show canonical strength totals with original source badges.
- [ ] Add AI fact query UI with pending suggestion approval/reject states.
- [ ] Confirm mobile bottom nav, provider detail panels, setup actions, and AI suggestion controls do not overlap or resize unstable elements.
- [ ] Run render contract tests and JavaScript syntax check.

Verification:

```bash
python3 -m pytest tests/test_open_wearables_ui_contract.py tests/test_dashboard_render_contract.py -q
node --check static/js/app.js
```

### Task 7: Systematic UI Audit Loop

**Owner:** main controller plus UI QA agent

**Files:**
- Create: `docs/qa/FIT-245-open-wearables-hub/ui-audit.md`
- Create: screenshot/video artifacts under an ignored or intentionally tracked-safe QA location, depending on repo policy.
- Modify app files only for accepted defects found by this audit.

**Interfaces:**
- Consumes: completed implementation branch.
- Produces: proof that the app works across required interactive states before PR/merge.

**FIT-245 UI Proof Loop:**

Run this bounded loop after implementation is code-complete:

1. Observe: open the live app and inspect Settings, History, Stats, recommendations, and AI query flows on desktop and mobile.
2. Choose: select the highest-risk failed state from the audit matrix.
3. Act: make one scoped fix for that state.
4. Verify: rerun the exact failed state and the closest regression test.
5. Record: update `docs/qa/FIT-245-open-wearables-hub/ui-audit.md` with state, viewport, theme, result, evidence path, and fix commit.
6. Stop: only when every required state passes, the remaining blocker requires owner credentials/data, or the same blocker repeats three times with no new path forward.

- [ ] Start the local app from the implementation worktree on an available port.
- [ ] Run desktop light audit for:
  - Settings hub healthy.
  - Settings missing config.
  - Settings remote blocked.
  - Setup/check success.
  - Setup/check failure.
  - Manual sync success.
  - Manual sync failure.
  - Provider connected.
  - Provider stale.
  - Provider capability gap.
  - Recommendation source proof fresh.
  - Recommendation source proof stale.
  - Recommendation conflict warning.
  - History all workouts.
  - History canonical strength grouped view.
  - History source-specific drilldown.
  - Stats category totals.
  - AI fact question with evidence.
  - AI suggestion pending.
  - AI suggestion approve/reject.
- [ ] Run desktop dark audit for the same state families where theme affects readability.
- [ ] Run mobile light audit for nav, Settings hub/provider rows, provider detail state, History filters, recommendation proof, and AI suggestion controls.
- [ ] Run mobile dark audit for contrast and overlap on the same mobile state families.
- [ ] Validate empty states: no providers, no facts, no recommendation influence, no AI evidence.
- [ ] Validate blocked/error states: Open Wearables down, stale provider, invalid config, rejected host, AI query no evidence.
- [ ] Validate keyboard/focus behavior for setup/check, provider details, source proof drawer, AI suggestion approval, and modal/sheet close controls.
- [ ] Fix accepted audit defects and rerun affected checks.
- [ ] Record every passed, failed, blocked, and deferred state in the QA document.

Browser proof command pattern:

```bash
python3 app.py
```

Use browser automation or real Chrome interaction to capture evidence. Save only redacted screenshots and avoid exposing raw health values where not necessary.

### Task 8: Review, PR, Mergeability, And Linear Closeout

**Owner:** main controller

**Files:**
- Modify: `.superpowers/sdd/progress.md` if the implementation branch uses subagent-driven development progress tracking.
- Modify: PR description.
- Modify: Linear issue comments.

**Interfaces:**
- Consumes: final implementation diff, focused tests, UI audit document, independent review result.
- Produces: pushed branch, PR, review evidence, mergeability status, and Linear-visible closeout.

- [ ] Run complete focused test suite for touched surfaces.
- [ ] Run `git diff --check`.
- [ ] Inspect full diff for unrelated changes, secrets, raw payloads, generated runtime files, screenshots with private values, and scope creep.
- [ ] Run a second review pass against the implementation issue and this plan.
- [ ] Fix accepted/actionable review findings and rerun focused checks.
- [ ] Push branch and open or update PR.
- [ ] Ensure PR body includes:
  - What changed.
  - Why.
  - Linear issue.
  - Acceptance criteria checked.
  - UI audit evidence.
  - Tests.
  - Risk.
  - How to test.
  - What was intentionally not done.
  - Agent involvement.
  - Follow-up issues created.
- [ ] Post standalone review/test evidence comment on the PR.
- [ ] Check GitHub mergeability after every push.
- [ ] Do not call the branch ready while mergeability is `DIRTY`.
- [ ] Add Linear closeout comment with branch, PR, commit, tests, UI audit, review result, merge state, blockers, and follow-ups.

Final verification command set will depend on touched files, but must include at least:

```bash
git diff --check
python3 -m pytest tests/test_open_wearables_adapter.py tests/test_open_wearables_health_sync_redaction.py tests/test_wearable_fact_store.py tests/test_history_canonical_categories.py tests/test_recommendation_sources.py tests/test_ai_fact_context.py tests/test_open_wearables_ui_contract.py tests/test_dashboard_render_contract.py -q
node --check static/js/app.js
```

## Follow-Up Issue Boundaries

Create follow-up issues instead of folding these into the first slice unless the implementation issue explicitly includes them:

- Full Open Wearables runtime supervision from Fitness Dashboard.
- Downstream Open Wearables webhook ingestion with signature verification, idempotency, and replay protection.
- Removing direct Oura/Apple/WHOOP fallback code.
- Long-range provider parity proofs across every Open Wearables provider.
- Raw provider detail exploration UI.
- AI autonomous plan mutation.
- Recurring background automation beyond documented one-shot sync/manual sync behavior.

## Self-Review Checklist

- [ ] The implementation keeps Open Wearables as integration hub and Fitness Dashboard as coaching engine.
- [ ] The implementation has a Linear issue and branch before code edits.
- [ ] The implementation does not leak raw provider/Open Wearables data.
- [ ] The provider model is generic.
- [ ] The fact store stores normalized facts only.
- [ ] History merges local lifted and Watch/provider strength training through `strength_training`.
- [ ] Recommendation proof shows source/freshness/conflict and stays conservative.
- [ ] AI answers cite sanitized evidence and require approval for mutation.
- [ ] Existing direct-source fallbacks still work.
- [ ] UI audit evidence covers desktop/mobile, light/dark, interactive states, empty states, blocked states, warning states, and mobile overlap.
- [ ] PR and Linear closeout include tests, review, UI audit, mergeability, and follow-up boundaries.
