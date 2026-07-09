# FIT-245 Open Wearables Hub Design

## Goal

Make Open Wearables the only wearable ingestion path for Fitness Dashboard metrics. The owner's directive is explicit: data should come from Open Wearables only because it merges Oura, WHOOP, and Apple Health into one API.

Fitness Dashboard must remain the coaching engine. Open Wearables owns wearable provider breadth, provider connection state, normalized upstream access, and phone/provider ingestion. Fitness Dashboard consumes normalized daily facts from Open Wearables, stores only coaching-safe facts, preserves provenance, applies deterministic recommendation policy, and exposes sanitized evidence to UI and AI.

## Current-Reality Assessment - 2026-07-09

The owner's mental model is directionally right for the target product, but today's Fitness Dashboard code is not there yet. Open Wearables exists as a hub and has the right external API shape, but the Fitness Dashboard bridge is still narrow, while direct Oura, WHOOP, and Apple Health paths remain durable sources.

| Area | Owner mental model | Current reality | Evidence | Verdict |
| --- | --- | --- | --- | --- |
| Hub role | Open Wearables already merges Oura, WHOOP, and Apple Health into one source. | Fitness Dashboard can talk to Open Wearables, but the app bridge fetches only `events/sleep`, `events/workouts`, and `summaries/activity` for the last 7 days. | `app.py:11248-11270`, `/Users/admin/open-wearables/backend/app/api/routes/v1/events.py:21-67`, `/Users/admin/open-wearables/backend/app/api/routes/v1/summaries.py:22-73` | Bridge exists, but not full OW-only ingestion. |
| Sync response | Open Wearables data flows into the app. | `/api/health/sync` returns sanitized metadata counts/errors only and does not persist facts; `/api/open-wearables/sync` persists only selected coarse facts. | `app.py:11609-11623`, `app.py:11655-11680`, `app.py:12244-12257` | Metadata-only bridge still exists. |
| Fact storage | Fitness Dashboard stores normalized facts from the hub. | `wearable_fact_store.py` stores profile-scoped daily facts and rejects raw/secret-looking fields, but the current fact schema is minimal. | `wearable_fact_store.py:15-27`, `wearable_fact_store.py:30-49`, `wearable_fact_store.py:80-116`, `wearable_fact_store.py:205-250` | Good local seam; schema needs expansion. |
| OW persisted facts | OW facts cover wearable coaching needs. | Current OW bridge stores at most steps, resting HR, active minutes, sleep duration, and sleep average HR from the latest activity/sleep data. | `app.py:11772-11810`, `/Users/admin/fitness-dashboard/.claude/worktrees/dazzling-golick-7cd594/docs/prd/10-open-wearables-integration.md:120-139` | Not enough parity for retiring direct sources. |
| Oura | Oura is absorbed by Open Wearables. | Direct Oura still uses `OURA_API_TOKEN`, Oura v2, and local SQLite `oura_daily`/`oura_sleep`; recommendations still read Oura cache directly. | `oura_client.py:44-68`, `oura_client.py:240-263`, `oura_client.py:279-347`, `app.py:2919-2972` | Direct path remains primary recovery/sleep source until parity. |
| WHOOP | WHOOP is absorbed by Open Wearables. | Direct WHOOP has OAuth, protected token material, sync runs, normalized records, daily facts, CSV import, freshness, and recommendation conflict handling. | `whoop_store.py:66-151`, `whoop_store.py:182-213`, `app.py:12827-12844`, `app.py:12877-12931`, `/Users/admin/fitness-dashboard/.claude/worktrees/dazzling-golick-7cd594/docs/prd/09-whoop-integration.md:7-16` | Direct durable WHOOP path remains. |
| Apple Health | Apple Health is absorbed by Open Wearables. | Health Auto Export webhook still writes to `apple_health_sync.db`; legacy file routes still read `~/Documents/Health`; Open Wearables has a separate phone SDK invite path. | `apple_health_parser.py:774-878`, `apple_health_parser.py:879-929`, `health_ingest.py:1-20`, `/Users/admin/open-wearables/docs/providers/apple-health.mdx:14-20` | Direct HAE path remains until OW phone path proves parity. |
| Provider/source visibility | Legacy direct sources disappear once OW exists. | `wearable_sources` still returns WHOOP, Oura, Apple Health, then Open Wearables; some stale legacy summary copy is hidden only under specific source conditions. | `app.py:12260-12284`, `app.py:12935-12970`, `app.py:11877-11900` | FIT-267-style hiding is partial UI treatment, not source retirement. |
| Profile scoping | OW user mapping is profile-safe. | Profile key is derived from current data user; config stores `profiles`; mapping changes clear OW facts and recommendation cache. | `app.py:10258-10317`, `app.py:10320-10338`, `/Users/admin/fitness-dashboard/.claude/worktrees/dazzling-golick-7cd594/docs/prd/10-open-wearables-integration.md:289-299` | Recent profile isolation is real and must be preserved. |
| Open Wearables capability | OW can serve the required normalized model. | OW exposes events, summaries, data sources, OAuth providers, sync, SDK token/log/sync, and user invitation-code routers; docs list cloud and SDK providers. Some provider metrics are partial or coming soon. | `/Users/admin/open-wearables/backend/app/api/routes/v1/__init__.py:38-57`, `/Users/admin/open-wearables/docs/providers/supported.mdx:14-38`, `/Users/admin/open-wearables/docs/providers/coverage.mdx:17-40`, `/Users/admin/open-wearables/docs/architecture/unified-data-model.mdx:36-49` | Platform shape supports target; parity still needs proof metric by metric. |

## Product Stance

The target product should show one wearable system, not multiple competing source systems. Open Wearables is the backend hub and only wearable ingestion path. Fitness Dashboard is the coaching engine and normalized daily fact consumer.

User-facing product stance:

- Settings shows a Wearable Sources hub centered on Open Wearables, with provider-level rows for Oura, WHOOP, Apple Health/HealthKit, and any other OW-supported provider.
- History, Stats, recommendations, and AI answers use canonical normalized categories while preserving provider/source provenance.
- Recommendations explain which OW-derived facts influenced today's plan.
- AI can query sanitized facts and reconciled history, but any mutation requires explicit user approval.

Open Wearables must not become a raw-score override engine. Wearable data modifies confidence and recommendations only through Fitness Dashboard's deterministic, bounded recommendation policy.

## Scope

The epic covers:

- Guided Open Wearables setup.
- Visible Wearable Sources hub.
- OW-only wearable metric ingestion target architecture.
- Provider/source provenance and canonical normalized categories.
- Normalized daily fact storage in Fitness Dashboard.
- Metric ownership and conservative conflict handling.
- History/backfill migration from direct sources.
- AI query behavior over sanitized facts.
- Safety/privacy posture and raw-payload exclusion.
- Testing, rollout, and first implementation slice.

The first implementation slice must ship:

- Guided setup for a secured Open Wearables service.
- Visible Wearable Sources hub with provider rows.
- Recommendation proof showing OW-derived facts and conservative modifiers.
- Sanitized status/sync projection.
- Profile-scoped normalized daily fact storage from OW.
- Parity instrumentation comparing OW facts against existing direct Oura, WHOOP, and Apple Health data.

Out of scope for slice 1:

- Retiring direct Oura, WHOOP, Apple Health HAE, Apple Health legacy file, or WHOOP CSV paths.
- Supervising Open Wearables Docker/Postgres/Redis/Celery from Fitness Dashboard.
- Persisting raw provider payloads in Fitness Dashboard.
- Letting AI mutate records, mappings, or recommendations without approval.
- Treating OW health scores as direct recommendation authority.
- Replacing existing direct-source UI rows before parity evidence and owner approval.

## Single-Source Target Architecture

Target architecture:

1. Open Wearables ingests Oura, WHOOP, Apple Health/HealthKit, and future providers through its cloud OAuth, webhook, REST pull, mobile SDK, XML/import, and provider-specific sync mechanisms.
2. Open Wearables normalizes provider data into its unified data model: events, series, descriptors, device/source mappings, provider settings, and summaries.
3. Fitness Dashboard reads OW API summaries/events/sources for the current profile's mapped OW user.
4. Fitness Dashboard maps OW API records into its own normalized daily facts, storing only coaching-safe fields in `wearable_fact_store`.
5. Fitness Dashboard recommendations use metric ownership, freshness, conflicts, confidence, and manual training/user notes to produce conservative coaching.
6. UI and AI consume only sanitized fact/source projections.

Fitness Dashboard should not call Oura, WHOOP, Apple Health HAE, or legacy HealthKit file parsers for wearable metrics in the final target state. Those direct integrations may exist as compatibility fallbacks during migration only.

## Open Wearables API Surface To Consume

Fitness Dashboard should consume these OW external surfaces:

- Provider/source status: `GET /api/v1/users/{user_id}/data-sources`.
- Provider OAuth: `GET /api/v1/oauth/providers`, `GET /api/v1/oauth/{provider}/authorize`, and callback completion through the existing app bridge.
- Phone SDK setup: invitation-code/token/sync/log endpoints under the OW Mobile SDK routers.
- Workout events: `GET /api/v1/users/{user_id}/events/workouts`.
- Sleep events: `GET /api/v1/users/{user_id}/events/sleep`.
- Daily activity summary: `GET /api/v1/users/{user_id}/summaries/activity`.
- Daily sleep summary: `GET /api/v1/users/{user_id}/summaries/sleep`.
- Daily recovery summary: `GET /api/v1/users/{user_id}/summaries/recovery`.
- Body summary where needed: `GET /api/v1/users/{user_id}/summaries/body`.
- Data summary/counts for diagnostics: `GET /api/v1/users/{user_id}/summaries/data`.

[TBC] The current Fitness Dashboard bridge does not prove use of OW `summaries/sleep`, `summaries/recovery`, `summaries/body`, `timeseries`, or health-score endpoints. It currently fetches sleep events, workout events, and activity summaries only.

## Normalized Daily Fact Schema

Fitness Dashboard stores normalized daily facts only. Open Wearables may retain raw provider detail according to its own configuration, but Fitness Dashboard must not store raw OW/provider payloads.

Required fact fields:

- `profile_key`
- `fact_date`
- `provider_id`
- `provider_display_name`
- `source_system` = `open_wearables`
- `source_record_id` or stable OW event/summary id when available
- `source_record_kind` = `summary`, `event`, `series_rollup`, or `health_score`
- `metric_domain` = `sleep`, `recovery`, `activity`, `load`, `body`, or `training_history`
- `metric_name`
- `value`
- `unit`
- `band` or `score_state`
- `confidence`
- `freshness_state`
- `capability_state`
- `conflict_state`
- `used_for_recommendation`
- `source_observed_at`
- `source_last_synced_at`
- `imported_at`
- `original_label`
- `canonical_category`
- `provenance` with provider/source names only, not raw payloads or provider user ids

Minimum target metric domains:

- Sleep: duration, efficiency, sleep score, sleep debt, stage minutes, awake time, nap/main-sleep flag where available.
- Recovery: recovery score/band, HRV, resting HR, respiratory rate, skin temperature, SpO2, readiness proxies where available.
- Activity/load: steps, active calories, active minutes, strain/load, workout duration, HR zones, workout energy, workout HR, acute/chronic load inputs where available.
- Body: weight and related OW-supported body metrics where available.
- Training history: workout event, original activity label, canonical category, duration, source, load/volume when available.

Current local fact store fields are narrower than this target and should be migrated additively. It already rejects fields named like tokens, raw payloads, samples, records, and user ids.

## Sync Cadence And Freshness

Slice 1 cadence:

- Manual setup/check and manual sync from Settings.
- Status refresh when Settings or source proof loads.
- Recommendation reads the local fact cache and does not trigger broad OW backfills in the hot path.

Target cadence:

- Initial backfill: 30-90 days per provider where OW can serve it, capped by provider limits and run outside hot-path recommendation requests.
- Regular sync: at most once per app-open/session for manual foreground use, plus owner-approved scheduled sync only after the manual flow is stable.
- Recommendation read path: local cached facts only, with stale/missing/conflict state visible.

Freshness semantics:

- `fresh`: source data point is under 24 hours old.
- `aging`: source data point is 24 to under 48 hours old.
- `stale`: source data point is 48 hours or older.
- `missing`: provider is connected or expected but no usable fact exists.
- `blocked`: setup/auth/host/provider capability prevents sync.
- `error`: sync attempted and failed with a stable safe code.
- `capability_gap`: provider or OW does not support this metric.
- `not_authorized`: provider connected but scope/permission for metric is missing.
- `not_applicable`: metric does not apply to the provider/source.

Connected never means current. Freshness is based on data-point time first, sync attempt second.

## Error Taxonomy

Open Wearables-facing errors returned by Fitness Dashboard must use stable safe codes:

- `ow_missing_config`
- `ow_auth_missing`
- `ow_auth_failed`
- `ow_user_mapping_missing`
- `ow_user_mapping_verification_failed`
- `ow_remote_requires_tls`
- `ow_remote_host_not_allowed`
- `ow_provider_not_connected`
- `ow_provider_capability_gap`
- `ow_provider_not_authorized`
- `ow_provider_stale`
- `ow_sync_failed`
- `ow_timeout`
- `ow_malformed_response`
- `ow_no_facts`
- `ow_profile_scope_mismatch`

Error responses may include safe provider/source names, timestamps, counts, and remediation hints. They must not include raw exception bodies, auth headers, provider payload fragments, hostnames beyond redacted base URLs already approved for display, tokens, provider user ids, or raw OW response bodies.

## Source Ownership And Conflict Handling

The target source rule is simple: wearable metrics come from Open Wearables only. Within OW, provider/source provenance remains first-class.

Metric ownership still exists inside Fitness Dashboard because multiple OW providers can report overlapping metrics:

- Sleep duration and stage ownership prefers the provider with the strongest current sleep support and fresh data.
- Recovery/readiness ownership prefers scored recovery facts when available, but manual soreness/injury and recent workout completion stay higher authority than wearable optimism.
- Activity/load ownership prefers Apple Health/HealthKit workout completion when fresh through OW, then OW provider workout events with sufficient duration/load evidence.
- Conflict handling prevents any one provider from pushing the plan harder when another credible provider says to hold back.

If two fresh OW-backed sources differ by a meaningful band, Fitness Dashboard shows a source conflict and chooses the conservative plan. The conflict state is stored as a fact-level and recommendation-level property.

## History Model And Canonical Categories

History should combine Fitness Dashboard local records and Open Wearables workout events. The app adds canonical normalized categories while preserving original labels and source ids.

Examples mapping to `strength_training`:

- `Lifted`
- `Functional Strength Training`
- `Traditional Strength Training`
- provider-specific strength labels

The UI can show provenance such as `Strength - Logged`, `Strength - Apple Health via Open Wearables`, or `Strength - WHOOP via Open Wearables`.

This is not destructive rewriting. Original source labels and source ids remain auditable. AI answers and recommendation logic use canonical categories for counting/trends and provenance for source-specific claims.

## AI Query And Authority

AI coach context may contain:

- freshness block
- sanitized wearable sources
- normalized wearable facts
- normalized history rows
- source/freshness/conflict evidence

AI coach context must not contain:

- raw Open Wearables payloads
- raw provider payloads
- tokens or credentials
- auth headers
- provider user ids
- raw export records
- screenshots or logs with private health values outside the approved UI proof surface

Allowed AI behavior:

- Answer source-aware history and trend questions.
- Explain recovery/load/sleep relationships with freshness/conflict caveats.
- Suggest category mappings or cleanup actions with evidence.
- Suggest training-plan changes for user approval.

Not allowed:

- Read raw OW/provider payloads.
- Store raw provider payloads in prompts or logs.
- Auto-apply category mappings.
- Auto-change recommendation policy.
- Auto-change a training plan based only on AI interpretation.

Suggestions must include date range, source, freshness, conflict state, and affected records. Applying a suggestion requires explicit user approval.

## Safety And Privacy

The security posture from the old draft remains mandatory:

- `/api/health/sync`, `/api/open-wearables/sync`, and future hub endpoints return sanitized metadata only: counts, timestamps, source names, and stable error codes.
- Raw Open Wearables/provider payloads never appear in user-facing API responses, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- Open Wearables credentials and API keys remain server-only and are referenced through opaque local secret references where possible.
- Remote Open Wearables hosts require TLS and host allowlisting; localhost-only remains the safe default.
- Provider conflict is a first-class state, not an exception.
- Provider capability gaps show as `not provided by this source`, not as missing user data.
- AI answers must cite source/freshness/conflict state and avoid claims when evidence is stale or incomplete.

If Open Wearables is down, stale, missing a provider, or returning incomplete data, Fitness Dashboard keeps working from local cached normalized facts and any direct-source fallbacks that have not yet been retired. Recommendations lower confidence instead of pretending the hub is healthy.

## Migration Plan

Migration must be staged and conservative. Direct paths are demoted only after OW parity is proven with deterministic tests, fixture-backed comparison, and real-data proof when credentials/data are available.

### Stage 0 - Spec And Inventory

- Document current direct-source behavior and OW bridge gaps.
- Map every direct metric consumed by recommendations, history, Stats, Settings, and AI.
- Seed implementation issues from this spec.
- Do not change runtime behavior.

### Stage 1 - OW Setup, Hub UI, Facts, And Recommendation Proof

- Keep all direct paths live.
- Expand OW sync mapping into profile-scoped daily facts.
- Show provider rows under the OW hub while preserving direct source rows as fallback/legacy.
- Add recommendation proof that shows OW-derived facts, provider provenance, freshness, and conservative modifier behavior.
- Add parity instrumentation comparing OW facts against direct Oura, WHOOP, and Apple Health values where both exist.

Nothing is retired in slice 1. Direct Oura, direct WHOOP OAuth/API, WHOOP CSV import, Apple Health HAE webhook, Apple Health legacy file routes, and direct source proof remain active.

### Stage 2 - Provider Parity Proof

For each provider, prove parity before demotion:

- Oura parity: OW must provide readiness/recovery equivalent, sleep duration/stages/score, HRV, resting HR, temperature/skin-temperature context, steps/activity fields currently used by the dashboard, and sufficient history/backfill. Direct Oura cache remains fallback until OW covers all recommendation and Vitals uses.
- WHOOP parity: OW must provide recovery score/band, strain/load, sleep performance/sleep debt, workout energy/load, HRV, resting HR, respiratory rate, SpO2, skin temp, score/calibration state, reauth/error states, and equivalent source-conflict behavior. CSV import remains fallback until there is an OW-supported path or owner-approved replacement for historical CSV-only data.
- Apple Health parity: OW HealthKit/SDK path must provide workouts, steps, active energy, HR/RHR/HRV, sleep, workout HR where enabled, setup state, data-through freshness, and multi-workout dedupe behavior. HAE webhook remains fallback until phone SDK invite/sync reliability is proven.

Historical data rule: existing direct-source SQLite/JSON data remains read-only fallback and parity source until migrated or retired. Do not drop historical direct data in this epic.

### Stage 3 - Demote Direct Paths To Fallback

After parity is proven per provider:

- Default recommendations, source proof, AI facts, History, Stats, and Vitals to OW facts.
- Hide direct-source summary copy when OW facts are fresh and equivalent.
- Keep direct path available only as explicit fallback/diagnostic with stale/fallback labeling.
- Continue comparing direct vs OW facts for a bounded burn-in period.

### Stage 4 - Retire Direct Paths

Retire a direct path only after:

- OW parity tests are green.
- Real or fixture-backed migration proof exists.
- A rollback path is documented.
- Owner approves the retirement issue.
- Historical data has either been migrated into normalized fact history or intentionally retained as archived read-only evidence.

Retirement means no hot-path reads, no setup UI primary path, no recommendation authority, and no AI fact source. It does not require deleting old data.

## Related Issues To Reference

- `FIT-242`: WHOOP ingestion architecture. Use its metric/source/security constraints when designing WHOOP parity; do not duplicate the whole WHOOP direct implementation here.
- `FIT-253`: pairing UI. Use for provider connection UX and setup states.
- `FIT-261`: token storage. Use for OW credentials, Apple Health setup-token handling, and direct-source demotion security.
- `FIT-300`: metadata naming. Use for source/provider labels and status copy.
- `FIT-271`: source proof. Use for recommendation provenance, evidence text, and test expectations.
- `FIT-267`: stale legacy source hiding. Treat as UI cleanup that complements, but does not replace, migration.

## Testing

Backend tests should cover:

- Missing OW config, invalid URL, remote non-TLS, remote host not allowed, auth failure, profile mapping missing/mismatch, provider not connected, provider capability gap, timeout, malformed response, and no facts.
- Sanitized sync/status responses with no raw payload, tokens, secrets, user ids, auth headers, or provider internals.
- Normalized fact upsert, schema migration, profile scoping, idempotency, freshness states, conflict states, capability states, and recommendation-use markers.
- OW metric mapping for Oura, WHOOP, and Apple Health parity fixtures.
- Direct-source fallback remains active in slice 1.
- Demotion gates prevent direct-source retirement before parity proof.
- Recommendation source proof and conservative conflict behavior.
- AI fact context redaction and suggestion-only mutation.

Frontend tests should cover:

- Wearable Sources hub renders OW hub row and provider rows.
- Guided setup states: missing config, invalid URL, unsafe remote, unhealthy service, connected service, no providers, provider connected, provider stale/error, provider capability gap.
- Direct-source rows remain visible as fallback/legacy in slice 1.
- History filters group canonical strength categories across logged and OW workout sources.
- Source badges remain visible after category grouping.
- Recommendation explanation renders source, metric, modifier, conflict, and stale-state copy.
- AI suggestions require approval before mutation.

Browser QA should cover desktop/mobile and light/dark:

- Settings Wearable Sources hub.
- Provider detail drawer/sheet.
- Setup/check/pair/invite flows.
- History grouped strength view.
- Stats trend view using canonical categories.
- Recommendation explanation with source proof.
- AI question over sanitized facts.
- Missing/stale/conflict/capability-gap states.
- Mobile nav overlap and detail panels.

## Rollout

Slice 1: OW-only target foundation without retiring direct paths.

- Guided setup.
- Visible hub/provider UI.
- Sanitized status/sync projection.
- Profile-scoped normalized fact cache.
- Recommendation explanation proof.
- Parity instrumentation against direct sources.
- AI sanitized query proof.

Slice 2: provider parity expansion.

- Oura parity.
- WHOOP parity including CSV historical data strategy.
- Apple Health/HealthKit parity including HAE replacement criteria.
- Metric ownership and conflict policy hardening.

Slice 3: fallback demotion.

- Default user-facing wearable metrics to OW facts.
- Keep direct paths as explicit fallback/diagnostic only.
- Burn-in direct-vs-OW comparison.

Slice 4: retirement.

- Retire direct paths one provider at a time after owner approval.
- Preserve or migrate historical data.
- Remove direct path authority from recommendations, AI, History, Stats, Vitals, and Settings.

## Follow-Up Issue Seeds

1. **OW fact schema expansion and sanitized mapper.** Expand `wearable_fact_store` and the Open Wearables mapper to cover the target fact fields for sleep, recovery, activity/load, body, and training history. Acceptance criteria: schema migration is additive; forbidden raw/secret fields are rejected; mapper covers OW sleep/activity/recovery/body/workout fixtures; `/api/wearable-facts` remains sanitized and profile-scoped.

2. **Guided Open Wearables setup and provider hub UI.** Make Settings show OW as the wearable hub with provider rows for cloud and SDK providers, safe setup/check actions, and direct-source fallback labels. Acceptance criteria: missing/blocked/connected/stale/capability-gap states render on desktop/mobile light/dark; direct Oura/WHOOP/Apple rows are labeled fallback/legacy in slice 1; no secrets or raw values are exposed.

3. **Oura parity bridge through Open Wearables.** Map OW Oura sleep/recovery/activity data into normalized facts and compare it with the direct Oura SQLite cache. Acceptance criteria: parity report covers readiness/recovery, sleep score/duration/stages, HRV, RHR, temperature, steps/activity; recommendations still fall back to direct Oura when OW is missing; no Oura direct retirement occurs.

4. **WHOOP parity bridge through Open Wearables.** Map OW WHOOP recovery, strain/load, sleep, workout, and scoring-state data into normalized facts and compare it with `whoop.sqlite3`. Acceptance criteria: parity report covers recovery band/score, strain, sleep performance/debt, workout energy/load, HRV, RHR, respiratory rate, SpO2, skin temp, score state; CSV-only data remains visible as fallback; no direct WHOOP retirement occurs.

5. **Apple Health/HealthKit parity bridge through Open Wearables.** Map OW Apple Health/HealthKit workouts, steps, active energy, HR/RHR/HRV, and sleep into normalized facts and compare against HAE sync DB and legacy file sources. Acceptance criteria: parity report covers setup state, data-through freshness, multi-workout dedupe, strength category mapping, and workout HR behavior where enabled; HAE remains fallback.

6. **Recommendation source proof from OW facts.** Update recommendation source proof to use OW facts as the preferred wearable evidence while keeping direct-source fallback proof in slice 1. Acceptance criteria: source proof names provider provenance, metric, freshness, conflict, and conservative modifier; optimistic wearable conflicts cannot harden a plan; stale or missing OW facts lower confidence.

7. **AI sanitized fact query over OW facts.** Ensure AI fact context and query answers use OW facts, canonical history, and source/freshness evidence without raw payloads or mutation authority. Acceptance criteria: context excludes raw/secret fields; answers include date range/source/freshness/uncertainty; approve/reject only changes suggestion status unless a separate mutation issue exists.

8. **Provider parity burn-in dashboard.** Add a developer/diagnostic report comparing OW facts to direct Oura, WHOOP, and Apple Health values during migration. Acceptance criteria: report uses counts, dates, metric names, safe deltas/bands, and stable error codes only; it has fixture-backed tests; it explicitly marks unknown/unverifiable metrics as `[TBC]`.

9. **Direct-source fallback demotion gates.** Add runtime gates and tests that prevent direct Oura, WHOOP, Apple Health HAE, or legacy HealthKit paths from being demoted unless provider-specific parity criteria are satisfied. Acceptance criteria: each provider has an independent gate; slice 1 defaults keep direct sources active; attempted retirement without parity fails tests.

10. **Historical data migration and archive plan.** Define and implement how existing Oura SQLite, WHOOP SQLite/CSV-imported facts, Apple Health sync DB, and legacy file-derived facts are migrated or archived after OW parity. Acceptance criteria: no data is deleted; migrated records preserve provenance and original labels; archived direct stores are read-only and excluded from AI/raw exports.

11. **OW-only provider retirement: Oura.** After Oura parity burn-in and owner approval, demote then retire direct Oura hot-path reads. Acceptance criteria: recommendations, Vitals, History/Stats, source proof, and AI facts read OW facts; rollback to direct Oura is documented; old Oura data remains archived or migrated.

12. **OW-only provider retirement: WHOOP.** After WHOOP parity burn-in and owner approval, demote then retire direct WHOOP OAuth/API authority. Acceptance criteria: OW facts replace WHOOP recommendation signals; CSV-only historical data has an approved archive/migration path; direct token material is no longer required for normal operation.

13. **OW-only provider retirement: Apple Health.** After OW HealthKit/SDK parity and owner approval, demote then retire HAE webhook and legacy file authority. Acceptance criteria: OW phone source handles setup/freshness/workouts/sleep/activity; HAE is fallback only during burn-in; old sync DB records remain archived or migrated.

## Acceptance Criteria For FIT-245

- Covers product stance, architecture, data flow, history, AI query behavior, safety/privacy, testing, rollout, first implementation slice, and migration.
- Security posture is explicit: no raw OW/provider payloads in user-facing APIs, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- First build slice is guided setup, visible Wearable Sources hub, and recommendation proof.
- Provider/source provenance and canonical normalized categories are preserved.
- Captured decisions are honored: OW is the backend hub; Fitness Dashboard stays the coaching engine and stores normalized daily facts only; source conflicts use metric ownership plus conservative coaching; AI queries sanitized facts and requires user approval before mutation.
- Current-reality assessment is dated 2026-07-09 and names the gap between OW-only target and today's direct-source code.
