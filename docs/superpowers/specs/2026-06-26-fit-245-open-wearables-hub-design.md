# FIT-245 Open Wearables Hub Design

## Goal

Make Open Wearables the main wearable integration hub for Fitness Dashboard without moving coaching authority, raw health payloads, or safety policy out of Fitness Dashboard.

Open Wearables should own provider breadth, provider connection state, and normalized upstream access across its supported provider set. Fitness Dashboard should own the daily coaching decision: source precedence, freshness, dedupe, conflict handling, normalized training history, recommendation modifiers, AI-visible facts, and user-facing explanations.

## Current Evidence

- `origin/main` already includes a redacted Open Wearables sync response contract: `/api/health/sync` returns sanitized metadata through `_open_wearables_sync_metadata`, not raw fetched data.
- `/api/vitals` already prefers Open Wearables for heart rate, sleep, and activity before falling back to Oura data.
- Open Wearables is a separate self-hosted service with provider adapters, normalized REST APIs, provider capabilities, outgoing webhooks, and separate auth surfaces. It is not a small library to vendor into the Flask app.
- Existing History already mixes local lifted workouts with Apple Watch workout imports. The UI currently separates `Lifted` and `Functional Strength Training`, even though both should count as strength training for trends, recommendations, and AI answers.

## Product Stance

Fitness Dashboard should show one coherent wearable system, not a pile of unrelated integrations. Open Wearables is the hub for wearable inputs. Fitness Dashboard remains the coaching engine.

The user-facing product should make Open Wearables visible where trust matters:

- Settings shows Open Wearables as the hub with provider-level child rows.
- History and Stats use normalized training categories while preserving source labels.
- Recommendations explain which wearable facts influenced today's plan.
- AI answers can reason over sanitized wearable and training history facts, but cannot mutate records without approval.

Open Wearables should not become a raw-score override engine. Wearable data modifies confidence and recommendations only through Fitness Dashboard's bounded recommendation policy.

## Scope

The epic covers:

- Guided Open Wearables setup.
- Hub-level health, auth, and provider connection status.
- Provider capability display for all Open Wearables-supported providers.
- Normalized wearable fact ingestion and storage in Fitness Dashboard.
- Canonical training categories for history and AI query behavior.
- Conservative conflict handling between sources.
- End-user recommendation proof when Open Wearables-derived data changes a plan.
- AI read/query behavior over sanitized facts, with suggestions requiring approval.

The first implementation slice should ship:

- Guided setup in Settings for a secured Open Wearables service.
- Visible Wearable Sources hub with provider rows.
- Sanitized Open Wearables status/sync projection.
- Normalized daily fact cache.
- Canonical training category support for strength history.
- History/Stats grouping that merges local lifted workouts and Watch functional strength under one `strength_training` family.
- Recommendation explanation showing when Open Wearables-derived data changed today's plan.
- AI query path over sanitized facts and reconciled history, with suggestion-only authority.

Out of scope for the first implementation slice:

- Owning or supervising the Open Wearables Docker/Postgres/Redis/Celery runtime from Fitness Dashboard.
- Replacing all direct Oura, Apple Health, or WHOOP fallback paths.
- Persisting raw provider payloads in Fitness Dashboard.
- Letting AI automatically rewrite categories, source mappings, or training plans.
- Treating Open Wearables health scores as direct recommendation authority.

## Architecture

Open Wearables runs as a separate secured service. Fitness Dashboard integrates through an adapter layer instead of copying provider code.

Recommended layers:

1. `open_wearables_adapter`: talks to Open Wearables, handles base URL, auth reference, service health, provider status, sync triggers, and normalized fetches.
2. `wearable_fact_store`: stores Fitness Dashboard normalized daily facts, source metadata, freshness, score state, confidence, conflicts, and recommendation-use markers.
3. `history_normalization`: maps local and provider workout labels into canonical categories while preserving original source labels.
4. `recommendation_sources`: applies metric ownership, freshness gates, conservative conflict handling, and plain-language explanations.
5. `ai_fact_query`: exposes a read-only/suggestion-only query layer over sanitized facts and reconciled history.

Open Wearables configuration stored by Fitness Dashboard should include:

- Service base URL.
- Auth method and opaque API-key or token reference, never the raw token in user-facing output.
- Mapped Open Wearables user id.
- Last health check state.
- Last successful sync timestamp.
- Provider connection summaries.
- Provider capabilities and known gaps.

## Provider Scope

The product model should cover all Open Wearables-supported providers, not only WHOOP, Oura, and Apple Health. The UI and storage model should not assume every provider supports the same connection, sync, history, or metric behavior.

Provider rows should expose capability-aware states:

- Provider connected or not connected.
- Sync model: pull, webhook, SDK, file import, or unsupported.
- Available domains: sleep, recovery, HRV, resting HR, workouts, strain/load, activity, body, or other supported domains.
- Historical limit or backfill constraint when known.
- Last successful provider sync.
- Missing capability versus missing user data.

The first implementation can prove the flow with a small number of real providers, but the model should be provider-generic from the start.

## Data Flow

Open Wearables is the source aggregator. Fitness Dashboard is the decision filter.

Flow:

1. Fitness Dashboard checks Open Wearables service health and provider connection state.
2. Fitness Dashboard triggers sync or observes a downstream sync signal for enabled providers.
3. Fitness Dashboard fetches normalized summaries/events from Open Wearables.
4. The adapter maps those records into Fitness Dashboard normalized daily facts.
5. The fact store dedupes and labels facts by provider, metric, source freshness, score state, capability, and confidence.
6. The recommendation engine applies metric ownership and conservative conflict handling.
7. The UI explains which source was used, what changed, and whether conflict or staleness lowered confidence.

All provider data exposed to UI or AI must go through Fitness Dashboard's sanitized fact layer. Raw Open Wearables/provider payloads must not appear in user-facing API responses.

## Normalized Facts

Fitness Dashboard should store normalized daily facts only. Open Wearables can retain raw provider detail in its own system according to its own raw-payload configuration, but Fitness Dashboard should keep the minimum needed for coaching, history, and audit.

Core fact fields:

- `fact_date`
- `provider`
- `source_system`
- `source_record_id`
- `metric_domain`
- `metric_name`
- `value`
- `unit`
- `score_state`
- `freshness_state`
- `confidence`
- `capability_state`
- `conflict_state`
- `used_for_recommendation`
- `imported_at`
- `source_observed_at`
- `original_label`
- `canonical_category`

Expected domains:

- Sleep: duration, efficiency, sleep score, sleep debt, REM/deep/light/awake when available.
- Recovery: recovery score/band, HRV, resting HR, respiratory rate, skin temperature, SpO2 where available.
- Load/activity: steps, active calories, active minutes, strain/load, workout duration, HR zones where available.
- Body: weight and related provider-supported metrics when available.
- Training history: workout event, source type, canonical category, original label, duration, volume/load where available.

## Source Ownership And Conflict Handling

Fitness Dashboard should use metric ownership plus conservative coaching.

Metric ownership gives each metric a preferred source path when multiple fresh sources are available. Conflict handling prevents any one wearable from pushing the plan harder when another reliable signal says to hold back.

Default policy:

- Open Wearables is the preferred hub source when it has fresh normalized facts.
- Existing direct Oura, Apple Health, and WHOOP paths remain fallback/legacy until the hub proves parity.
- Apple Health or Watch-derived workouts are strong evidence for completed activity/load.
- WHOOP-derived recovery/strain/sleep can inform recovery and readiness, but cannot independently upgrade training ambition.
- Oura-derived sleep/recovery remains useful when fresh, especially during Open Wearables gaps.
- Manual workout logs and injury/soreness notes remain higher authority than wearable optimism.

If two fresh sources differ by a meaningful band, Fitness Dashboard should show a source conflict and choose the conservative plan.

## History Model

History should come from both Fitness Dashboard local records and provider workout events through Open Wearables.

The app should add canonical normalized categories while preserving original labels. For example:

- `Lifted`
- `Functional Strength Training`
- `Traditional Strength Training`
- provider-specific strength labels

These should map to `strength_training` for History, Stats, recommendations, and AI answers. The UI can still show provenance such as `Strength - Logged`, `Strength - Watch`, or `Strength - WHOOP via Open Wearables`.

This is a canonical category layer, not destructive rewriting. Original source labels and source ids remain auditable.

History/Stats should answer category-level questions correctly:

- How often did I strength train?
- How much Watch strength activity did I do compared with logged lifting?
- Did recovery drop after hard strength days?
- Are provider workouts duplicating local logged workouts?

## AI Query And Authority

The AI coach can query sanitized facts and reconciled history. It can answer questions, summarize trends, and propose changes. It cannot silently mutate records or rules.

Allowed:

- Answer source-aware history questions.
- Explain trend and recovery relationships with freshness/conflict caveats.
- Suggest category mappings or cleanup actions with evidence.
- Suggest training-plan changes for user approval.

Not allowed:

- Read raw Open Wearables/provider payloads.
- Store raw provider payloads in prompts or logs.
- Auto-apply category mappings.
- Auto-change recommendation policy.
- Auto-change a training plan based only on an AI interpretation.

AI suggestions should include evidence such as date range, source, freshness, and affected records. Applying a suggestion requires explicit user approval.

## UI Design

Settings should include a Wearable Sources hub:

- Hub row: Open Wearables service health, last successful sync, auth/config state, and setup/check action.
- Provider rows: provider status, capability summary, last sync, freshness, errors, and whether the provider contributed to the latest recommendation.
- Safe setup flow: base URL, auth/API key reference, mapped user id, health check, provider connection checks, and missing-config guidance.

History should group by canonical category by default, with source filters still available. `Lifted` and `Functional Strength Training` should contribute to the same strength-training totals while preserving source badges.

Recommendation UI should include end-user proof when wearable data changed a plan:

- Which source was used.
- What metric changed the plan.
- What recommendation modifier was applied.
- Whether conflict or stale data lowered confidence.

Example copy:

- `Strength reduced today because recovery was low from WHOOP via Open Wearables.`
- `Using Watch workout history for recent load; Oura sleep is stale.`
- `Source conflict: WHOOP recovery is green, Oura readiness is low. Using the conservative plan.`

## Safety And Privacy

The hub must fail visibly and conservatively.

Hard rules:

- `/api/health/sync` and future hub endpoints return sanitized metadata only: counts, timestamps, source names, and stable error codes.
- Raw Open Wearables/provider payloads never appear in user-facing API responses, AI prompts, logs, exports, screenshots, PR text, or Linear comments.
- Open Wearables credentials and API keys remain server-only and are referenced through opaque local secret references.
- Remote Open Wearables hosts require TLS and host allowlisting; localhost-only remains the safe default.
- Provider conflict is a first-class state, not an exception.
- Provider capability gaps show as `not provided by this source`, not as missing user data.
- AI answers must cite source/freshness/conflict state and avoid claims when evidence is stale or incomplete.

If Open Wearables is down, stale, missing a provider, or returning incomplete data, Fitness Dashboard should keep working from local cached facts and existing direct-source fallbacks. Recommendations lower confidence instead of pretending the hub is healthy.

## API Direction

Potential endpoints for implementation issues:

- `GET /api/open-wearables/status`
- `POST /api/open-wearables/setup/check`
- `POST /api/open-wearables/sync`
- `GET /api/open-wearables/providers`
- `GET /api/wearable-sources`
- `GET /api/wearable-facts`
- `GET /api/history/categories`
- `GET /api/ai/facts/context`
- `POST /api/ai/suggestions/<id>/approve`

Existing routes to extend:

- `/api/vitals`
- `/api/dashboard`
- `/api/freshness`
- `/api/recommendation/smart`
- `/api/next-workout`
- History and Stats APIs used by the History tab.

## Testing

Backend tests should cover:

- Missing Open Wearables config.
- Invalid and unavailable hub service.
- Successful health check.
- Provider capability mapping.
- Sanitized sync/status response with no raw payload, tokens, secrets, user ids, or provider internals.
- Normalized fact upsert and idempotency.
- Canonical category mapping for `Lifted`, `Functional Strength Training`, and provider strength labels.
- Deduping local workouts against provider workout events.
- Fresh, aging, stale, error, unscored, calibrating, conflicting, ignored, and capability-gap states.
- Recommendation modifier proof and conservative conflict behavior.
- AI query context redaction and suggestion-only mutation.

Frontend tests should cover:

- Wearable Sources hub renders hub row and provider rows.
- Guided setup states: missing config, invalid URL, unhealthy service, connected service, no providers, provider connected, provider stale/error.
- History filters group strength training across local and provider sources.
- Source badges remain visible after category grouping.
- Recommendation explanation renders source, metric, modifier, conflict, and stale-state copy.
- AI suggestions require approval before mutation.

Browser QA should cover desktop and mobile:

- Settings Wearable Sources hub.
- Provider detail drawer or sheet.
- History grouped strength view.
- Stats trend view using canonical categories.
- Recommendation explanation with source proof.
- AI question over history.
- Missing/stale/conflict states.
- Light/dark readability and mobile nav overlap.

## Rollout

Use an epic plus implementation slices.

Slice 1: Open Wearables hub foundation and end-user proof.

- Guided setup.
- Visible hub/provider UI.
- Sanitized status/sync projection.
- Normalized fact cache.
- Canonical strength category mapping.
- History grouping proof.
- Recommendation explanation proof.
- AI sanitized query proof with suggestions requiring approval.

Slice 2: Provider expansion and parity.

- Add provider-specific capability refinements.
- Expand beyond the initial real provider proof.
- Compare hub facts against existing direct Oura/Apple/WHOOP paths.
- Keep direct fallbacks until parity is proven.

Slice 3: Deeper history and AI workflows.

- Richer history timelines.
- Trend questions over recovery, load, sleep, and training.
- Approval flows for suggested mappings and data cleanup.

Slice 4: Optional webhook/sync automation.

- Add downstream Open Wearables webhook ingestion only after signature verification, idempotency, and replay protection are specified and tested.

## Follow-Up Issues

Create separate implementation issues from this spec:

1. Open Wearables hub setup and provider status UI.
2. Normalized wearable fact store and sanitized sync/status projection.
3. Canonical training categories for History/Stats.
4. Recommendation source proof and conservative conflict handling.
5. AI fact query layer and approval-required suggestions.
6. Provider capability matrix and expansion beyond first real provider proof.

## Acceptance Criteria For FIT-245

- This design is committed on a dedicated branch/worktree.
- The design preserves Open Wearables as the integration hub and Fitness Dashboard as the coaching engine.
- The design includes all user-approved decisions from the brainstorming session.
- The design includes the current redacted Open Wearables sync security posture.
- The design can seed follow-up Linear implementation issues without another product-shape round.
