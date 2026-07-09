# FIT-245 Open Wearables Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Fitness Dashboard toward Open Wearables as the only wearable ingestion path while keeping direct Oura, WHOOP, and Apple Health paths active until provider-specific parity is proven.

**Architecture:** Open Wearables is the backend wearable hub. Fitness Dashboard stores profile-scoped normalized daily facts only, preserves provider provenance, applies deterministic coaching policy, and exposes sanitized source proof to UI and AI. Direct Oura, WHOOP, Apple Health HAE, Apple Health legacy file, and WHOOP CSV paths are migration fallbacks until explicit retirement issues complete.

**Tech Stack:** Python 3, Flask, SQLite, pytest, existing app templates/CSS/JavaScript, Open Wearables FastAPI API, browser QA with desktop/mobile and light/dark proof.

## Global Constraints

- Linear spec issue: `FIT-245`.
- Revised design file: `docs/superpowers/specs/2026-06-26-fit-245-open-wearables-hub-design.md`.
- No raw Open Wearables/provider payloads in user-facing APIs, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- Fitness Dashboard stores normalized daily facts only.
- Open Wearables is the target only wearable ingestion path.
- Direct Oura, WHOOP, Apple Health HAE, Apple Health legacy file, and WHOOP CSV paths stay active in slice 1.
- Retirement is provider-specific and requires parity proof plus owner approval.
- Preserve provider/source provenance and canonical normalized categories.
- AI can query sanitized facts; mutation requires user approval.
- Source conflicts use metric ownership plus conservative coaching.
- Remote Open Wearables hosts require TLS and host allowlisting.
- UI work follows the existing app style first and `/Users/admin/.codex/ui/DESIGN.md` only as fallback.
- QA must cover desktop/mobile, light/dark, setup states, provider details, empty states, blocked states, warning states, recommendation proof, AI facts, and mobile overlap.

---

### Task 0: Issue Boundaries And Worktree Setup

**Files:**
- Read: `docs/superpowers/specs/2026-06-26-fit-245-open-wearables-hub-design.md`
- Read: current direct-source PRDs under `/Users/admin/fitness-dashboard/.claude/worktrees/dazzling-golick-7cd594/docs/prd/`
- Create: one Linear issue per follow-up seed selected for implementation.

**Interfaces:**
- Produces: one implementation issue, one branch/worktree, and scoped acceptance criteria for exactly one slice.

- [ ] Read the revised spec and choose one follow-up issue seed.
- [ ] Confirm the implementation issue contains the selected seed title, scope, acceptance criteria, and non-goals.
- [ ] Confirm the worktree is scoped to that one issue before edits.
- [ ] Confirm direct-source retirement is out of scope unless the selected issue is a retirement issue.

### Task 1: OW Fact Schema Expansion And Mapper

**Files:**
- Modify: `wearable_fact_store.py`
- Modify: `app.py`
- Test: `tests/test_wearable_fact_store.py`
- Test: `tests/test_open_wearables_sync.py`
- Test: `tests/test_open_wearables_health_sync_redaction.py`

**Interfaces:**
- Produces: expanded `WearableDailyFact` fields or compatible dict payloads for `metric_domain`, `source_system`, `source_record_kind`, `capability_state`, `source_observed_at`, `source_last_synced_at`, `original_label`, `canonical_category`, and safe provenance.
- Preserves: existing `list_wearable_sources()` and `list_recommendation_facts()` callers.

- [ ] Write tests that reject forbidden fields: `authorization`, `access_token`, `refresh_token`, `token`, `password`, `secret`, `raw`, `payload`, `samples`, `records`, and `user_id`.
- [ ] Write tests for profile-scoped idempotent upsert across the expanded fact fields.
- [ ] Write mapper tests for OW activity, sleep, recovery, body, and workout fixture shapes.
- [ ] Add additive schema migration columns without dropping existing facts.
- [ ] Map OW facts into normalized daily facts with provider/source provenance and no raw payload.
- [ ] Keep `/api/health/sync` metadata-only; only `/api/open-wearables/sync` stores facts.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_wearable_fact_store.py tests/test_open_wearables_sync.py tests/test_open_wearables_health_sync_redaction.py -q
```

### Task 2: Guided Setup And Wearable Sources Hub

**Files:**
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `static/js/app.js`
- Modify: `static/css/app.css`
- Test: `tests/test_open_wearables_ui_contract.py`
- Test: `tests/test_dashboard_render_contract.py`

**Interfaces:**
- Consumes: `/api/open-wearables/status`, `/api/open-wearables/setup`, `/api/open-wearables/setup/check`, `/api/open-wearables/providers`, `/api/wearable-sources`.
- Produces: OW hub row, provider child rows, setup/check actions, pair/invite actions, direct-source fallback/legacy labels.

- [ ] Write render contract tests for missing config, blocked remote URL, unhealthy hub, connected hub, no providers, provider connected, provider stale, provider error, capability gap, and restart-required state.
- [ ] Render Open Wearables as the hub and Oura/WHOOP/Apple Health as provider rows beneath it when available through OW.
- [ ] Preserve direct Oura, WHOOP, and Apple Health rows as fallback/legacy in slice 1.
- [ ] Ensure all setup error copy uses stable safe codes and does not expose secrets or raw provider values.
- [ ] Verify mobile setup/provider detail states do not overlap bottom nav.
- [ ] Run focused tests and JS syntax check:

```bash
python3 -m pytest tests/test_open_wearables_ui_contract.py tests/test_dashboard_render_contract.py -q
node --check static/js/app.js
```

### Task 3: Provider Parity Instrumentation

**Files:**
- Modify: `app.py`
- Create or modify: `open_wearables_parity.py`
- Test: `tests/test_open_wearables_parity.py`

**Interfaces:**
- Consumes: normalized OW facts, direct Oura cache, direct WHOOP daily facts, Apple Health sync/file summaries.
- Produces: safe parity rows with provider, metric, date, OW state, direct state, safe delta/band, and `[TBC]` reason when comparison cannot be verified.

- [ ] Write tests for Oura parity rows: readiness/recovery, sleep score/duration/stages, HRV, RHR, temperature, steps/activity.
- [ ] Write tests for WHOOP parity rows: recovery, strain/load, sleep, workout energy/load, HRV, RHR, respiratory rate, SpO2, skin temp, score state.
- [ ] Write tests for Apple Health parity rows: workouts, steps, active energy, HR/RHR/HRV, sleep, strength category, multi-workout dedupe.
- [ ] Ensure parity output uses counts, dates, metric names, bands, and safe deltas only.
- [ ] Mark unavailable direct or OW evidence as `[TBC]` with observed blocker.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_open_wearables_parity.py -q
```

### Task 4: Recommendation Source Proof From OW Facts

**Files:**
- Modify: `recommendation_sources.py`
- Modify: `app.py`
- Modify: `whoop_recommendations.py` only if source-conflict integration requires it.
- Modify: `static/js/app.js`
- Test: `tests/test_recommendation_sources.py`
- Test: `tests/test_whoop_source_conflicts.py`
- Test: `tests/test_apple_health_recommendation_bridge.py`

**Interfaces:**
- Consumes: `list_recommendation_facts()` and provider parity/freshness.
- Produces: recommendation source proof that names OW provider provenance, freshness, conflict, metric, and conservative modifier.

- [ ] Write tests proving fresh OW sleep/recovery/load facts can lower intensity or confidence.
- [ ] Write tests proving stale/missing/capability-gap OW facts are display-only.
- [ ] Write tests proving optimistic OW facts cannot harden a plan when another credible source conflicts.
- [ ] Keep direct WHOOP/Oura/Apple proof available as fallback/legacy in slice 1.
- [ ] Update UI proof copy to say when OW-derived facts changed the plan.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_recommendation_sources.py tests/test_whoop_source_conflicts.py tests/test_apple_health_recommendation_bridge.py -q
```

### Task 5: Canonical History And Category Provenance

**Files:**
- Modify: `history_normalization.py`
- Modify: `app.py`
- Modify: `static/js/app.js`
- Test: `tests/test_history_canonical_categories.py`
- Test: `tests/test_history_detail_and_analyze.py`
- Test: `tests/test_dashboard_render_contract.py`

**Interfaces:**
- Produces: normalized history rows with `canonical_category`, `original_label`, and source/provenance fields for logged and OW workout events.

- [ ] Write tests proving `Lifted`, `Functional Strength Training`, `Traditional Strength Training`, and provider strength labels map to `strength_training`.
- [ ] Write tests proving original labels, source labels, source ids, dates, and duration remain visible.
- [ ] Write tests preventing date-only dedupe from hiding legitimate two-a-day workouts.
- [ ] Add OW workout events to history only through normalized/provenance-preserving rows.
- [ ] Keep local logged workouts authoritative and non-destructively grouped.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_history_canonical_categories.py tests/test_history_detail_and_analyze.py tests/test_dashboard_render_contract.py -q
```

### Task 6: AI Sanitized Fact Query

**Files:**
- Modify: `ai_fact_query.py`
- Modify: `app.py`
- Modify: `static/js/app.js`
- Test: `tests/test_ai_fact_context.py`

**Interfaces:**
- Consumes: freshness, wearable source proof, normalized facts, normalized history.
- Produces: AI fact context and answer evidence over OW facts without raw payloads.

- [ ] Write tests proving AI context excludes raw payloads, token-like fields, auth headers, provider user ids, and private config.
- [ ] Write tests proving answers include date range, source, freshness, conflict state, and uncertainty.
- [ ] Write tests proving suggested mutations remain pending until explicit approval.
- [ ] Prefer OW facts in AI context; include direct-source fallback facts only with fallback/legacy labeling during migration.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_ai_fact_context.py -q
```

### Task 7: Provider Demotion Gates

**Files:**
- Create or modify: `open_wearables_retirement_gates.py`
- Modify: direct-source read paths only for selected provider gate.
- Test: `tests/test_open_wearables_retirement_gates.py`

**Interfaces:**
- Produces: provider-specific gate functions for `oura`, `whoop`, and `apple_health`.

- [ ] Write tests proving each provider cannot be demoted without parity proof.
- [ ] Write tests proving demotion is independent per provider.
- [ ] Write tests proving slice 1 keeps all direct paths active.
- [ ] Add gate checks before any provider retirement issue changes recommendation, AI, History, Stats, Vitals, or Settings authority.
- [ ] Run focused tests:

```bash
python3 -m pytest tests/test_open_wearables_retirement_gates.py -q
```

### Task 8: UI QA And Safety Proof

**Files:**
- Create: `docs/qa/FIT-245-open-wearables-only/ui-audit.md`
- Modify app files only for accepted defects found by the audit.

**Interfaces:**
- Produces: redacted QA evidence for desktop/mobile, light/dark, and interactive states.

- [ ] Start the app with an isolated `DATA_DIR`.
- [ ] Audit Settings hub/provider states: missing config, blocked host, setup success, setup failure, provider connected, stale, error, capability gap, pair route, phone invite route.
- [ ] Audit recommendation proof: fresh OW fact, stale OW fact, source conflict, direct fallback/legacy visible.
- [ ] Audit History/Stats canonical category and source badge behavior.
- [ ] Audit AI fact question, no-evidence answer, pending suggestion, approve, and reject.
- [ ] Audit empty states, blocked states, warning states, mobile nav overlap, and dark/light contrast.
- [ ] Save only redacted screenshots or textual proof.
- [ ] Run relevant render tests after every accepted UI fix.

### Task 9: Provider Retirement Issues

**Files:**
- Modify only the selected provider's direct-source authority paths.
- Test: selected provider parity, recommendation, AI, History/Stats, Vitals, Settings, and fallback tests.

**Interfaces:**
- Consumes: provider-specific parity proof, demotion gate, owner approval.
- Produces: OW-only authority for exactly one provider.

- [ ] Confirm the issue is one of Oura retirement, WHOOP retirement, or Apple Health retirement.
- [ ] Confirm owner approval and parity evidence are recorded in the issue.
- [ ] Remove hot-path authority for only the selected provider.
- [ ] Keep historical data archived or migrated; do not delete old data.
- [ ] Document rollback and fallback behavior.
- [ ] Run the selected provider's full focused suite plus recommendation/source proof tests.

## Follow-Up Issue Boundaries

Use the issue seeds in the revised spec. Do not combine unrelated seeds in one implementation branch.

Retirement issues are explicitly not slice 1. They require provider-specific parity, burn-in evidence, and owner approval.

## Self-Review Checklist

- [ ] Open Wearables is the target only wearable ingestion path.
- [ ] Fitness Dashboard remains deterministic coaching authority.
- [ ] Slice 1 keeps direct Oura, WHOOP, Apple Health HAE, Apple Health legacy file, and WHOOP CSV paths active.
- [ ] Facts are normalized, profile-scoped, and sanitized.
- [ ] Provider/source provenance and canonical categories are preserved.
- [ ] Raw OW/provider payloads cannot reach APIs, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- [ ] Recommendation source proof is conservative and conflict-aware.
- [ ] AI queries sanitized facts and requires approval before mutation.
- [ ] Provider retirement is blocked without parity proof and owner approval.
