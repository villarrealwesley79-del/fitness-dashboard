# Fitness Dashboard — Handoff for a New Agent

Handoff written 2026-04-24 by Claude (Opus 4.7, 1M context). Purpose: give any agent enough context to pick this project up cold and make safe changes.

## One-paragraph summary

A Flask-based personal fitness dashboard with a heavy analytical UI ("AI Coach Feed") and an AI coach layer wired to LM Studio. The deterministic Python engine is the source of truth for workout prescription; the LLM is a translator/coach that produces *intent patches* the Python re-applies under strict safety rails. Apple Health data syncs via Health Auto Export; Oura ring data via the Oura Cloud API. Runs on a Mac mini, exposed over Tailscale HTTPS. Inference uses ASUS GX10 over Tailscale as primary and Mac mini LM Studio as fallback.

## Runtime surface

| Thing | Where |
| --- | --- |
| Project dir | `/Users/admin/.openclaw/workspace/projects/fitness-dashboard` |
| Live URL | `https://admins-mac-mini.tail6c6490.ts.net:5050` |
| Local URL (behind Tailscale) | `http://127.0.0.1:5050` |
| Flask process | launchd `com.fitness-dashboard`, `RunAtLoad=true`, `KeepAlive=true` |
| Flask plist | `~/Library/LaunchAgents/com.fitness-dashboard.plist` |
| Sync staleness watchdog | launchd `com.fitness-dashboard.staleness`, daily 09:15 local |
| Staleness log | `/tmp/apple-health-staleness.log` |
| Flask stdout/err | `/tmp/fitness-dashboard.log` |
| Venv | `./venv/bin/python3` |
| LLM primary | `http://100.99.46.2:1234` ASUS GX10, model `qwen/qwen3-30b-a3b-2507` |
| LLM fallback | `http://127.0.0.1:1234` Mac mini, model `qwen/qwen3.6-35b-a3b` |
| BobStudio | separate agent host; **don't** route LLM work to it — GPU collision with Charlie/Ralph/CMO |

### Restart / operate

```bash
# Reload after a launchd plist edit:
launchctl unload ~/Library/LaunchAgents/com.fitness-dashboard.plist
launchctl load   -w ~/Library/LaunchAgents/com.fitness-dashboard.plist

# Force Flask restart (launchd KeepAlive auto-respawns):
lsof -iTCP:5050 -sTCP:LISTEN | awk 'NR>1 {print $2}' | sort -u | xargs -r kill

# Tail the log:
tail -f /tmp/fitness-dashboard.log

# Verify it's up:
curl -sk -I https://admins-mac-mini.tail6c6490.ts.net:5050/ | head -1   # expect 302 → /login
```

## Secrets

| Secret | File | How provisioned |
| --- | --- | --- |
| Flask `SECRET_KEY` | `./.flask-secret` (0600) | Auto-generated on first boot, 128-char hex. Rotating it logs everyone out. Set `SECRET_KEY` env to override. |
| Apple Health webhook token | `./.health-sync-token` (0600) | Auto-generated on first boot. Env var `HEALTH_SYNC_TOKEN` wins if set. The dashboard's Settings → Integrations → Apple Health → Setup modal shows the tokenized URL. |
| Oura API token | `.env` (`OURA_API_TOKEN=…`, 0600) | User-provided. Loaded manually by `app.py` on boot. |

## Auth Model

The dashboard is single-owner by default (`FITNESS_DASHBOARD_SINGLE_USER=true`). Public `/register` is disabled after the first user exists, and authenticated non-owner users are rejected before they can reach personal workout, wearable, settings, or Apple Health data. Set `FITNESS_DASHBOARD_SINGLE_USER=false` only after data stores are made per-user.

## Architecture

### High level

```
Browser (https, Tailscale-TLS)
  │
  ▼
Mac mini — Flask (auth + routes + deterministic engine)
  │  reads: data_*.json (workouts, body, cardio, recovery, sleep, soreness, settings, baselines, nutrition)
  │  reads: oura_daily.sqlite3 (Oura Cloud sync)
  │  reads: apple_health_sync.db (HAE webhook + file-based export)
  │
  ├─→  (on explicit button press) ASUS LM Studio primary; Mac LM Studio fallback
  │       POST /v1/chat/completions (strict json_schema)
  │       returns: intent patch → Python validates + applies
  │
  └─→  (always) Oura Cloud API for HRV / RHR / sleep / readiness
```

### Source of truth rule

The **deterministic Python engine** (`generate_next_workout()` in `app.py:1250`) owns prescription. The LLM is never the brain. Three call sites are the ONLY places the LLM is invoked:

1. **Adjust Plan** — user tap, typed constraint. LLM returns intent patch. Python validates and applies under safety rails. Route: `POST /api/workout/adjust`.
2. **Analyze Workout** — post-session or historical. LLM returns narrative (summary/wins/concerns/comparison/cue). Read-only. Route: `POST /api/workout/analyze`.
3. **Auto-analyze** — fires once after Complete Workout with `latest=true`.

Everything else is deterministic Python.

## File map

### Python

| Path | Role |
| --- | --- |
| `app.py` | Flask routes, deterministic engine, safety rails, caching, metrics |
| `auth.py` | flask-login users, session hardening, SECRET_KEY resolution, public-prefix allowlist |
| `apple_health_parser.py` | HAE v2 JSON normalizer + legacy file parser, sync DB, dedup keys, sleep hour helper |
| `health_ingest.py` | older HealthKit ingest path (still registered; rarely used) |
| `oura_client.py` | Oura API wrapper + `oura_daily.sqlite3` schema |
| `oura_sleep_sync.py` | on-demand Oura pull |
| `data_loader.py` / `data_store.py` | JSON read/write helpers |
| `lm_studio_adapter.py` | LM Studio client (HTTP + json_schema), adjust/analyze schemas, validators |
| `stripe_checkout.py` | Stripe Pro tier (not actively used) |
| `workout-analysis.py` | one-off analysis script |

### Frontend

| Path | Role |
| --- | --- |
| `templates/index.html` | Single-page 8-tab UI (dash / vitals / next / log / history / body / stats / settings). Modals: Adjust, Swap, Analyze, Apple Setup, Active Workout. |
| `static/css/style.css` | Dark analytical theme, safe-area aware, SVG chart styling |
| `static/js/app.js` | Single IIFE, one boot, one tab loader. SVG chart helpers (sparkline/line/bar/donut/gauge). All API calls. |
| `static/manifest.json` / `static/js/sw.js` | PWA basics |

Cache-busting currently uses `style.css?v=20260427-bodycomposition-1`, `app.js?v=20260427-bodycomposition-1`. Bump the relevant tag in `templates/index.html` on every JS/CSS structural change.

### Data

| Path | Role |
| --- | --- |
| `data_workouts.json` | Primary lifting log. List of `{id, date, session_type, duration_minutes, exercises[{machine, muscle_group, sets:[{reps,weight_lbs,rpe}]}], notes, created_at, …}`. `id` is 12-char hex, backfilled for legacy rows. |
| `data_soreness.json` | `[{date, muscle, soreness_level (1-10), notes, created_at}]`. Muscle key: **`muscle`** (legacy: `muscle_group` / `body_part`). |
| `data_body.json` | Body-history: weight / body fat / measurements. |
| `data_cardio.json`, `data_recovery.json`, `data_nutrition.json`, `data_sleep.json` | Logs by type. |
| `data_settings.json` | Training goal, sessions per week, duration target, equipment preference, macro targets. |
| `data_baselines.json` | Starting weights per machine for auto-progression. |
| `oura_daily.sqlite3` | Oura Cloud cache. Table `oura_daily`. |
| `apple_health_sync.db` | HAE + file-based Apple Health. Table `ah_sync_log(id, source, record_type, record_date, record_key, data_json, created_at)` with `UNIQUE(source, record_type, record_date, record_key)`. Table `ah_sync_events` logs every accepted webhook attempt, including duplicate-only posts. |
| `ai_coach_cache.sqlite3` | LLM cache + metrics. Tables `adjust_cache`, `adjust_metrics`. |
| `auth.db` | Users + Stripe references. |
| `~/Documents/Health/healthkit_*.json` | Raw Apple Health file exports (legacy path). |

## API endpoints (greatest hits)

All require auth except the public-prefix allowlist. Auth is session-cookie via flask-login.

### Reads

| Route | Purpose |
| --- | --- |
| `GET /api/dashboard` | Headline numbers, next workout, body stats, readiness factors, nutrition, alerts, muscles, exercise progression |
| `GET /api/vitals` | Weight trend, HR, sleep, activity (Oura-first, Apple Health fallback) |
| `GET /api/history` / `GET /api/history-all` | Lifting history. `history-all` includes stable `id` + `created_at` on every row |
| `GET /api/body-history` | Body weight / fat / measurements trend |
| `GET /api/insights` | e1RM trends, muscle volume, push/pull ratio, insight cards |
| `GET /api/analytics/advanced` | Fatigue score, deload flag, recovery score |
| `GET /api/muscle-fatigue` | Per-muscle readiness heatmap |
| `GET /api/adherence` | Recommendation vs actual tracking |
| `GET /api/baselines` | Starting weights per exercise |
| `GET /api/exercises` | Full manual-logging exercise library |
| `GET /api/exercises/alternatives/<muscle>` | Equipment-filtered alternatives for swap UI |
| `GET /api/settings` | Goal config + available goals/time options/equipment options |
| `POST /api/settings` / `PUT /api/settings/equipment` | Update |
| `GET /api/protocols` | Pre-defined lean-gain/recomp protocols |
| `GET /api/recommendation/smart` | Readiness-aware recommendation with reasoning |
| `GET /api/weather` | wttr.in cache (10 min) |
| `GET /api/oura/status` | Today's Oura snapshot |
| `GET /api/oura/trends` | 7-day series |
| `GET /api/oura/sleep-summary` | Last night + week avg + trend |
| `POST /api/oura/sync-sleep` | Manual Oura pull |
| `GET /api/apple-health/summary` / `/workouts` / `/sleep` / `/steps` / `/vitals` | Apple Health slices |
| `GET /api/apple-health/sync/status` | **auth-gated**, includes `last_sync`, `last_attempt`, and the last event counts. Does **not** return token material. |
| `GET /api/apple-health/sync/setup-url` | **auth-gated owner setup path**, returns the tokenized HAE webhook URL for the explicit Setup modal only. |
| `POST /api/apple-health/sync` | **Public + token-gated** (`?token=` or `X-Sync-Token`). HAE webhook. |

### Writes

| Route | Purpose |
| --- | --- |
| `POST /api/add-workout` | Single-exercise logger |
| `POST /api/complete-workout` | Full workout save (assigns stable `id`), adherence calculation |
| `POST /api/add-soreness` | Stores `{date, muscle, soreness_level, notes}` |
| `POST /api/add-cardio` / `add-recovery` / `add-body-measurement` / `add-nutrition` | Type-specific logs |
| `POST /api/workout/swap` | Swap one exercise (deterministic) |
| `POST /api/workout/adjust` | **AI Adjust Plan** — LLM intent patch under safety rails |
| `POST /api/workout/analyze` | **AI Analyze Workout** — read-only narrative |
| `POST /api/delete-history` | Remove entries |
| `GET /api/export-backup` / `POST /api/import-backup` | JSON dump/restore |

### Meta

| Route | Purpose |
| --- | --- |
| `GET /api/ai/health` | LM Studio reachability + model loaded |
| `GET /api/ai/metrics?hours=24` | adjust_requests / ok / cache_hits / fallbacks / avg_latency_ms / recent[5] |

## Safety rails (Adjust Plan)

All enforced in `_apply_intent_patch` in `app.py`. Never weaken these without explicit Bob/Wesley approval.

| Rail | Spec | Verified live |
| --- | --- | --- |
| RPE delta | clamp ±1.0 | ✓ |
| RPE up-blocked on deload | meso week 4 or `meso_plan.name == 'Deload'` → one-way reduction | ✓ |
| RPE up-blocked on poor readiness | `oura_readiness < 60` → one-way reduction | ✓ |
| Sets delta | clamp ±20% | ✓ |
| Sets up-blocked on deload / low readiness | same | ✓ |
| Weight guard | ≤ 110% of recent e1RM per exercise | wired (no baselines yet) |
| Hard blacklist | muscles flagged with soreness ≥ 7/10 in last 48h, **plus** LLM-requested avoid list | ✓ (reads `muscle` / `muscle_group` / `body_part` for legacy compatibility) |
| LLM-proposed swap to blacklisted muscle | hard refuse with note | ✓ |
| Duration cap trim | drop tail exercises to fit `duration_cap_min` | ✓ |
| Drop cardio | set `recommendation.cardio = null` | ✓ |
| LM Studio unreachable | status=`fallback`, plan unchanged, UI chip "AI coach unavailable" | ✓ |
| Invalid JSON from LLM | validators raise `LmStudioError`, fallback | ✓ |
| Concurrency | `_INFERENCE_LOCK = Semaphore(1)` in adapter | ✓ |

Diff-style `applied_notes` go back to the UI: `"Removed: Lateral Raise — shoulders avoided"`, `"Ignored: RPE increase (+1.0) — deload week"`, etc.

## LLM schemas

### Adjust intent (LM Studio response_format: json_schema, strict=true)

```json
{
  "summary": "string",
  "intent": {
    "avoid_muscles": ["string"],
    "swap": [{"replace_exercise": "...", "target_muscle": "...", "reason": "..."}],
    "rpe_delta": -1.0..1.0,
    "sets_delta_pct": -20..20,
    "duration_cap_min": number,
    "drop_cardio": boolean
  }
}
```

Python re-validates via `_validate_adjust_intent()` in `lm_studio_adapter.py`. `_apply_intent_patch` is wrapped in `try/except` → fallback on any exception.

### Analyze narrative

```json
{
  "summary": "string",
  "wins": ["string"],
  "concerns": ["string"],
  "comparison": "string",
  "next_session_cue": "string"
}
```

Python re-validates via `_validate_analyze_result()`. Read-only; no plan mutation.

## Cache & metrics

| Table (in `ai_coach_cache.sqlite3`) | Content |
| --- | --- |
| `adjust_cache` | `cache_key → response_json`. Key: `sha1(workout_id + content_fingerprint + constraint + readiness_date + model_version + exercise_library_hash)`. Prefix: `adjust:` for adjust, `analyze:` for analyze. |
| `adjust_metrics` | `ts, outcome (ok/cache_hit/fallback), latency_ms, constraint_len, model_version, reason`. |

Top-right AI status pill in the header pulls from `/api/ai/health` every 60s + opens a popover with 24h metrics.

## Apple Health integration

Two parallel paths, both land in `ah_sync_log`:

1. **HAE REST API webhook** (`POST /api/apple-health/sync?token=…`). Normalized by `_normalize_hae_payload()` which accepts both HAE v2 wrapped format (`{data: {workouts, metrics}}`) and legacy flat format. Dedup key: `(source, record_type, record_date, record_key)` where `record_key` for workouts is the start timestamp.
2. **File-based import** from `~/Documents/Health/healthkit_*.json` — older path, still supported by `apple_health_parser.py`.

Setup flow is in `docs/HEALTH_AUTO_EXPORT_SETUP.md` (field-verified 2026-04-23 against the current HAE UI). The launchd staleness watchdog reads `apple_health_sync.db` directly because `/api/apple-health/sync/status` is intentionally auth-gated.

History tab merges Watch workouts with manually-logged lifts, with a de-dup: if the user logged a lift on a given day, Watch's own "Traditional Strength Training" entry is filtered out to avoid double counting.

## Conventions to respect when extending

1. **Never replace the deterministic engine with LLM output.** Intent patches, narrative, UI blurbs only.
2. **Always add Python-side validation for any new LLM-returned structure.** `strict=true` on LM Studio is belt; Python validation is suspenders.
3. **Bump cache-bust version** (`?v=…`) on every JS/CSS structural change, or Chrome will cache the old file.
4. **Don't add endpoints to the public-prefix allowlist without a token check.** Check `auth.py:_PUBLIC_PREFIXES`.
5. **Local-time dates, not UTC.** Use `today()` in JS (which now builds local YYYY-MM-DD) and `datetime.now().strftime("%Y-%m-%d")` in Python. The frontend's `fmtDate()` parses `YYYY-MM-DD` as local midnight.
6. **Workout IDs are stable.** Every new workout gets a 12-char hex UUID. `/api/history-all` surfaces it. Analyze button uses `workout_id` for disambiguation.
7. **LLM fallback is cheap and the right default.** If any step in the adjust/analyze pipeline raises, return `status=fallback` with the deterministic plan untouched.
8. **launchd, not nohup.** The Flask process is launchd-supervised. Killing `python3 app.py` triggers an auto-restart, which is the right way to pick up code changes.

## Known limitations (explicitly left open)

See `docs/AI_COACH_TODO.md` for the current list. Highlights:

- **HRV mis-mapped in legacy rows.** Pre-fix HRV records live under `record_type='heart_rate'` with `type='avg'`. Oura covers HRV for today's flows so user-visible impact is nil. Future: migrate into `record_type='hrv'`.
- **Side/joint granularity.** "Left shoulder sore" currently blocks ALL shoulder work. Next pass: add `joints_loaded` taxonomy to the exercise library so row/pull exercises aren't over-penalized.
- **HAE timestamp `[:10]` slice ignores TZ offset.** Only breaks for cross-timezone travel (user tz change vs Watch tz).
- **Token auth is enforced on `/api/apple-health/sync`.** Older docs may still claim otherwise; treat those as stale.
- **Cookie secure defaults to true.** Local HTTP dev needs `SESSION_COOKIE_SECURE=false` env var.
- **LM Studio `LAST_WORKOUT_RECOMMENDATION` is in-memory.** Flask restart loses the last in-flight Adjust patch. Cached intent survives (in SQLite).

## Audit history

Two automated audits ran on this session; both are reflected in the code.

1. **Codex gpt-5.4 high** — flagged 3 P0 / 7 P1 / 1 P2. All P0+P1 shipped. Transcript excerpted in `docs/SESSION_AUDIT.md`.
2. **Codex gpt-5.5 high** (second pass) — flagged 7 new P1 / 3 new P2 that 5.4 missed. All shipped. Bob independently verified the P0 + P1 fixes and logged them to `.memory.md` on his side.

Bob's final sign-off (2026-04-24 07:41 CDT): *"Nothing else. Call it done. This is a clean ship."*

## Verification checklist for the next agent

Before touching anything, run these to confirm the state:

```bash
# Port + process
lsof -iTCP:5050 -sTCP:LISTEN
launchctl list | grep com.fitness

# Clean boot log
tail -30 /tmp/fitness-dashboard.log   # expect "Debug mode: off"

# FD leak check; should stay low after repeated authenticated API requests
PID=$(lsof -tiTCP:5050 -sTCP:LISTEN)
lsof -p "$PID" | awk 'NR>1 && $9 ~ /auth.db/ {count++} END {print count+0}'  # expect 0 or low single digits

# Full smoke now checks AI primary/fallback routing and FD bounds too.
COOKIE=... bash support/self_test.sh

# Public endpoints
curl -sk -I https://admins-mac-mini.tail6c6490.ts.net:5050/   # 302 → /login

# Auth gate on status endpoint
curl -sk http://127.0.0.1:5050/api/apple-health/sync/status   # expect 401

# Sync token gate
curl -sk -X POST http://127.0.0.1:5050/api/apple-health/sync \
  -H 'Content-Type: application/json' -d '{}'                # expect 401

# AI coach reachability
curl -sk http://100.99.46.2:1234/v1/models                    # primary: expect 200 + qwen/qwen3-30b-a3b-2507 loaded
curl -sk http://127.0.0.1:1234/v1/models                      # fallback: expect 200 + qwen/qwen3.6-35b-a3b loaded

# Workout ID backfill
jq '.[] | select(.id == null)' data_workouts.json             # expect empty

# Apple Health latest insert / accepted webhook attempt
sqlite3 apple_health_sync.db "select max(created_at) from ah_sync_log;"
sqlite3 apple_health_sync.db "select max(created_at) from ah_sync_events;" # may be empty until next HAE post after 2026-04-24 fix
```

## Docs directory

| File | Purpose |
| --- | --- |
| `PROJECT_HANDOFF.md` | this file |
| `SESSION_AUDIT.md` | first-person audit of the build for reviewers |
| `AI_COACH_TEST_TRANSCRIPT.md` | live transcript of Adjust Plan test + safety-rail proofs |
| `AI_COACH_TODO.md` | known limitations + next-pass work |
| `HEALTH_AUTO_EXPORT_SETUP.md` | step-by-step HAE config against current iOS UI |
| `APPLE_HEALTH_SHORTCUT.md` | iOS Shortcut alternative (was abandoned but preserved) |
| `../visual-review/VISUAL_REVIEW.md` | original UI redesign review + screenshots narrative |

## Who's who

- **Wesley (user)** — owner of the dashboard and his own body. Makes product calls.
- **Bob** — Wesley's power of attorney for architecture decisions. Chat tab at `http://localhost:18789/chat`. Has already signed off on the current ship. Don't do major architecture changes without pinging Bob first.
- **Codex** — CLI-available at `codex exec` for code review. Use `-c model_provider="openai" -m gpt-5.5 -c model_reasoning_effort="high"` for the strongest pass; fall back to `gpt-5.4` if 5.5 is rate-limited.
- **Ralph / Charlie / CMO** — BobStudio agents. Don't schedule LLM inference on BobStudio while the dashboard is running — GPU collision with them.

## Quick "I need to add X" recipes

- **New API endpoint** → add route in `app.py`, add fetch in `static/js/app.js`, add UI in `templates/index.html` + `static/css/style.css`, bump the cache-bust version. Don't forget auth unless it's truly public.
- **New LLM-backed feature** → follow the Adjust Plan pattern: schema in `lm_studio_adapter.py`, validator function, strict `json_schema` response_format, Python re-validates, wrap the apply step in try/except → fallback.
- **New data type to sync from Apple Health** → add metric name to `_HAE_METRIC_MAP` in `apple_health_parser.py`, add the branch in `_normalize_hae_payload`, ensure the read endpoint uses the shared `_sleep_hours`-style helper pattern if it's aggregative.
- **New settings knob** → add to `data_settings.json` default, surface in `GET /api/settings` + `POST /api/settings`, wire in the Settings tab in `templates/index.html`.
- **Breaking change to safety rails** → do not do this without a sign-off from Bob or Wesley. Document the rationale in `AI_COACH_TODO.md` *before* touching the rail.
