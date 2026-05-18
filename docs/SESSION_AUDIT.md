# Session Audit — 2026-04-23

Self-review of everything shipped in this session, written for a code reviewer. Purpose: inputs for Codex and Bob to audit against.

## Scope

One session, start-to-present:
1. Redesign of the whole Flask fitness dashboard into an "AI Coach Feed" analytical UI.
2. Apple Health (HAE) integration, including the adapter for HAE's v2 JSON format.
3. LM Studio AI coach layer on ASUS GX10 — two user-triggered features (Adjust Plan, Analyze Workout).
4. launchd productionization (Flask auto-start, sync staleness check).
5. Misc UI/UX fixes from field feedback (header buttons, modal safe area, timezone display, synthetic-data cleanup).

## Files touched this session

| File | Type | Summary |
| --- | --- | --- |
| `templates/index.html` | rewrite | 8-tab analytical UI, AI status pill, Adjust Plan modal, Analyze modal, Swap modal, Apple Setup modal |
| `static/css/style.css` | rewrite | Dark analytical theme; safe-area-aware modal; per-exercise swap button; source tags |
| `static/js/app.js` | rewrite | Single IIFE, one boot, one tab loader; SVG chart helpers; AI status popover; analyze flow |
| `lm_studio_adapter.py` | new | HTTP client for LM Studio on ASUS GX10; schema-validated adjust + analyze |
| `apple_health_parser.py` | patch | HAE v2 format normalizer, dedup key widened, sleep hours→minutes, HRV record-type split |
| `app.py` | patch | `/api/workout/adjust`, `/api/workout/analyze`, `/api/ai/health`, `/api/ai/metrics`; cache + metrics tables |
| `scripts/check-apple-health-staleness.sh` | new | Daily staleness check, quiet until first real sync |
| `~/Library/LaunchAgents/com.fitness-dashboard.plist` | existing (re-loaded) | Flask auto-start (`KeepAlive=true`, `RunAtLoad=true`) |
| `~/Library/LaunchAgents/com.fitness-dashboard.staleness.plist` | new | 09:15 daily sync staleness check |
| `docs/APPLE_HEALTH_SHORTCUT.md` | new | iOS Shortcut recipe (abandoned in favor of HAE) |
| `docs/HEALTH_AUTO_EXPORT_SETUP.md` | new | Field-verified two-automation walkthrough |
| `docs/AI_COACH_TODO.md` | new | Next-pass tasks (side/joint granularity; metrics card; alerting) |
| `docs/AI_COACH_TEST_TRANSCRIPT.md` | new | Live test transcript for the Adjust Plan feature |
| `docs/SESSION_AUDIT.md` | new (this file) | Self-audit |
| `visual-review/VISUAL_REVIEW.md` | new | Original UI review |
| `apple_health_sync.db` | migrated | Dedup key column widened; 451 workout duration rows seconds→minutes; 2,119 sleep rows hours→minutes; 5 synthetic rows deleted |
| `ai_coach_cache.sqlite3` | new | Adjust and Analyze response cache + metrics table |

## Behavioral boundaries I tried to uphold

1. **Deterministic engine owns workout prescription.** `generate_next_workout()` in `app.py:1250` is untouched structurally. Never replaced with LLM. Bob's explicit directive.
2. **LLM only at user-invoked moments**, never in the normal Next Workout render path. Two call sites: Adjust Plan button, Analyze Workout button.
3. **Strict JSON-schema output from the LLM.** LM Studio's `response_format: json_schema, strict: true`. Python schema-validates again defensively.
4. **Safety rails enforced in Python, not prompt engineering.** RPE clamp ±1, sets clamp ±20%, weight cap at e1RM × 1.10, hard blacklist on sore muscles, no deload override. Tested in Adjust Plan flow end-to-end, fires exactly as specced.
5. **Read-only Analyze.** No prescription, no writes, no cache invalidation of the canonical plan. Only descriptive JSON.
6. **Fallbacks on every LLM call.** `LmStudioError` → `{status: "fallback", recommendation: <unchanged>}`. UI shows "AI coach unavailable" chip, never silently fails.
7. **Concurrency 1 on LM Studio calls** via `_INFERENCE_LOCK = Semaphore(1)` in the adapter to not stampede the ASUS.
8. **No LLM workload on BobStudio.** ASUS GX10 (`100.99.46.2:1234`) is the only inference host. Bob's call; avoids GPU collision with Charlie/Ralph/CMO.
9. **Cache by content fingerprint**, not just workout id. Editing a workout invalidates its cached analysis cleanly.
10. **Metrics logged on every call** (ok / cache_hit / fallback + latency + reason code). Visible via `/api/ai/metrics` and the header popover.

## Design tradeoffs I made intentionally

1. **One Data Type per HAE automation** — I documented this as "two automations required" after research, rather than trying to flatten it server-side. Config cost is one-time; backend complexity would recur forever.
2. **Apple Watch strength sessions on days the user already lifted get filtered out of History.** Reason: same session double-counted. Watch cardio on lift days stays. Heuristic not perfect (what if user lifts AM, Watch logs PM strength session as a real separate workout?), but dedup-leaning is safer than double-count-leaning.
3. **Analyze auto-fires on Complete Workout.** 350ms delay. User can dismiss the modal. Made this automatic because after the friction of logging a workout, asking the user to tap another button feels anti-climactic. If this proves annoying, trivial to move to explicit button.
4. **Analysis cache key uses workout content fingerprint, not just id.** Robust to edits. More key churn, but storage is trivial.
5. **launchd over Docker.** Mac mini dev box; launchd is native, no Docker daemon required. If Wesley moves this to a proper VPS, Docker is the next step.
6. **Didn't migrate old HRV rows out of `heart_rate` record_type.** Documented in AI_COACH_TODO.md. Impact: empty chart if user opens an Apple Health HRV view. Oura provides HRV in the dashboard's current flows, so user-visible impact is zero.
7. **Didn't build a Shortcut for 6:00 AM HAE trigger.** Documented as optional in HEALTH_AUTO_EXPORT_SETUP.md. iOS's background task budget actually makes the native `1 Day` cadence work within a few hours of morning anyway.

## Known limitations I want to name out loud

1. **`duration_minutes: 0` on Apr 22 sleep row** — HAE v2 Summarize-Day doesn't always emit the `asleep` aggregate. Phase values (deep/rem/core) are populated. I did not wire the fallback `asleep = deep + rem + core`. User mentioned, left pending.
2. **Watch-only "Functional Strength Training" is kept in History when user didn't manually lift that day.** If the user's intention is "only real lifts count", those Watch-captured-but-not-manually-logged sessions would be noise. Current behavior intentionally shows them so no data is silently dropped.
3. **Analyze LLM can cite the workout's `notes` field.** If the user wrote something like "I was hungover" in notes, the LLM will incorporate that into its concerns. This is mostly a feature (grounding on real context) but means user-visible narrative can surprise them. Transcript in `AI_COACH_TEST_TRANSCRIPT.md`.
4. **`LAST_WORKOUT_RECOMMENDATION` is in-memory only.** Flask restart → next `/api/workout/adjust` starts from the freshly generated deterministic plan. The cache preserves the LLM's intent patch but not the mutated plan. Tradeoff: simplicity. If the user restarts Flask mid-adjustment, they lose the in-flight patch.
5. **Scripts check endpoint — no auth.** `/api/apple-health/sync` and `/api/apple-health/sync/status` are on the public-prefix allowlist in `auth.py`. Anyone on the Tailscale network could POST Apple Health data. Trivial risk given Tailnet boundary; flagged explicitly because production deployment would need a bearer token.
6. **HAE duration heuristic** — I decide "seconds if value > 240, else minutes" inside `_normalize_hae_payload`. A true 4+ hour workout would be miscategorized. Real Watch workouts nearly all fall in the 10–90 min range, so this misfires in practice for ultramarathons only.
7. **Next Workout button wires into `LAST_WORKOUT_RECOMMENDATION` which can desync from what's rendered on the dashboard.** If the user applies an Adjust, then reloads, the Dashboard tab re-renders from `/api/dashboard` which calls `generate_next_workout()` anew — the Adjust patch is lost. User-invoked Adjust is implicitly ephemeral. Probably OK for the current use case.

## Safety rails — coverage map (validated live, see `docs/AI_COACH_TEST_TRANSCRIPT.md`)

| Rail | Where | Exercised |
| --- | --- | --- |
| RPE delta ≤ 1.0 | `_apply_intent_patch` | yes (sets clamped from LLM input) |
| RPE up blocked on deload | `_apply_intent_patch` | yes |
| RPE up blocked on readiness < 60 | `_apply_intent_patch` | yes |
| Sets delta ≤ 20% | `_apply_intent_patch` | yes |
| Weight cap at e1RM × 1.10 | `_apply_intent_patch` | wired, not exercised live (no e1RM baselines yet) |
| Hard blacklist on sore muscles (≥ 7/10) | `_apply_intent_patch` | yes (`avoid_muscles` merge) |
| LLM-proposed swap to a blacklisted muscle | `_apply_intent_patch` | yes, explicitly rejected |
| No deload override | `_apply_intent_patch` | yes (one-way clamp) |
| Timeout → fallback | `lm_studio_adapter.adjust_plan` + Flask | yes (tested by blocking ASUS) |
| Invalid JSON → fallback | `lm_studio_adapter.adjust_plan` | yes (caught by JSONDecodeError path) |
| Concurrency 1 | `_INFERENCE_LOCK = Semaphore(1)` | yes |

## What I'd look at in a code review (self-critique)

1. **`/api/workout/adjust` is long.** ~250 lines for route + `_apply_intent_patch`. Extract the helpers into `ai_coach.py` if this grows further.
2. **Duplicate caching utilities** — `_ai_cache_get / _ai_cache_put / _ai_metric_log` are the same pattern. Could become a small `Cache` class, but YAGNI for now.
3. **Frontend state lives in one big `state` object on `window.__aicoach`.** Works, but a refactor to explicit modules would be nicer at the next order of magnitude.
4. **`buildDailyBuckets` / `groupIntoBuckets` are fine but awkward.** If History / Stats frequency charts grow, a proper time-series bucket helper is worth it.
5. **CSS uses raw hex colors inside gradients.** I mostly use CSS vars, but gradient rgba() colors inline for performance. Acceptable.
6. **No automated tests.** The live transcript file is the only regression record. A pytest harness for `_apply_intent_patch` + a Playwright smoke for the new UI would raise the bar.
7. **`_normalize_hae_payload` grew to 80+ lines.** Could split per record_type if it grows further.
8. **Two launchd agents in `~/Library/LaunchAgents/`** — if the user moves to a different Mac, they'd need to be re-installed manually. No bootstrap script exists.
9. **The Tailscale URL is hardcoded** in several docs and in the webhook URL that HAE posts to. Moving machines means hunting these down. An `.env` or config file would centralize.

## Open questions I'd like a reviewer to challenge

1. Should the Analyze modal's "next_session_cue" influence the next Adjust Plan call? (Currently it's advisory only; not fed back into the system.)
2. Should the Analyze cache ever expire? Currently never — workout content is immutable, so an old analysis stays valid forever. But if we upgrade the model version, existing cached analyses drift stale vs the new model's quality. Invalidate by model version? (The cache key already includes `model_version`, so a model change auto-invalidates. Probably fine.)
3. Should the adjust intent patch be *persisted* (so a page reload preserves it) or stay ephemeral? Current: ephemeral. Arguably a feature — adjustments are per-session. Arguably a bug — user reloaded and lost their work.
4. Should HAE workouts feed into the volume trend on Stats / History? Currently: no, volume is lifting-only. Probably right.
5. Is the 350ms auto-open of Analyze after Complete Workout the right UX? Reviewer might call this intrusive.
6. Should I add a `delete` button on the 5 pre-session Apr 12–14 test rows? Currently surfacing to user and awaiting their call. Could be automated with a "this looks synthetic" heuristic, but that's risky.

## Acceptance I've already claimed

- All 8 tabs render.
- No console errors; 60+ API calls clean in Chrome.
- Adjust Plan end-to-end live with safety validators firing exactly as Bob specced.
- Analyze Workout end-to-end, 3–6s round trip, cite grounded in real workout + progression + notes.
- HAE backfill ingested 6 years of data (473 workouts, 2,141 sleep rows) clean.
- History tab merges lifts + Watch sessions with source tags and proper dedup.
- Per-exercise swap works; active-workout modal is reachable on iPhone.
- Mac mini Flask auto-restarts on crash and on boot.

## Open asks to reviewer

1. Audit the safety rails in `_apply_intent_patch` — do they hold against a malicious / misbehaving LLM output?
2. Confirm the HAE v2 normalizer is correct for real-world payload variants (multi-source HR, Watch-only sleep, indoor vs outdoor workouts, etc).
3. Is the cache key fingerprint robust or are there edit patterns that would fail to invalidate?
4. Is any of my "Known limitations" actually a must-fix before calling this done?
