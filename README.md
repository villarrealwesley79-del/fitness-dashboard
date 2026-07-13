# Fitness Dashboard

Fitness Dashboard is a local-first fitness and recovery app for planning workouts, tracking training execution, and turning wearable/health signals into practical daily guidance.

The product direction is to become a trusted daily fitness operating system: the user should know what to train, how hard to train, what recovery signals matter, and how food choices affect the rest of the day.

## What It Does

- Plans and adjusts strength workouts based on readiness, soreness, recent volume, and user constraints.
- Tracks active workouts, set completion, swaps, notes, and workout history.
- Surfaces recovery context from Oura, Apple Health / Health Auto Export, WHOOP, Open Wearables, and local cached data.
- Supports nutrition and body-composition tracking.
- Plans toward photo-based food logging, where a user can snap a picture of food and the app updates calorie/macronutrient context and daily coaching guidance.
- Keeps sensitive runtime data local by default.

## Current Repo Contents

This GitHub repo is a sanitized project copy. It intentionally does not include the old local git history because that history contained private runtime artifacts.

Included:

- Flask application source
- Templates and static assets
- Oura, Apple Health, WHOOP, Open Wearables, nutrition, workout, and AI-coach integration code
- Deployment entry files
- Product planning docs in `docs/`

Excluded:

- `.env` files
- auth databases
- SQLite databases
- local health exports
- Oura/Apple Health caches
- WHOOP SQLite data and protected OAuth material
- local JSON workout/health data
- logs, backups, virtualenvs, and generated artifacts

## Key Docs

- `docs/VISION.md` describes the target product direction and ideal end state.
- `docs/PRD.md` defines the product requirements and roadmap.
- `docs/CURRENT_STATE.md` captures the current app state, risks, and known gaps.
- `docs/RELEASE_RUNBOOK.md` documents release, restart, rollback, cache-bust, and Apple Health bridge checks.
- `docs/REPO_HYGIENE.md` documents which stale/runtime/generated artifacts stay out of Git.

## Running Locally

Create a Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the Flask app:

```bash
python app.py
```

On the Mac mini, install or refresh the local launchd agents with:

```bash
bash scripts/install-launchd-agents.sh --dry-run
bash scripts/install-launchd-agents.sh install
```

This manages both `com.fitness-dashboard` and
`com.fitness-dashboard.staleness` in `~/Library/LaunchAgents`.

Inspect the local runtime without changing it:

```bash
bash scripts/fitness-status.sh
```

The status command reports both launchd agents, the listener PID, `DATA_DIR`,
the redacted last Apple Health staleness line, and smoke-test prerequisites.

Run the authenticated smoke test against a local server:

```bash
BASE_URL=http://127.0.0.1:5050 \
FITNESS_SMOKE_USERNAME=<owner-username> \
FITNESS_SMOKE_PASSWORD=<owner-password> \
bash support/self_test.sh
```

Run the same smoke against a Tailscale/public URL by changing `BASE_URL`. If you
already have a session cookie and do not want the script to log in, pass
`COOKIE=<session-cookie-value>` instead of the username/password pair. The smoke
checks authenticated dashboard, settings, history, Oura, Apple Health sync
status, smart recommendation, AI health, a safe rejected workout write path, and
the existing file-descriptor leak regression.

The smoke script creates per-run temporary cookie/body files and removes them on
success, failure, or signal. `COOKIE_JAR` and `BODY_FILE` can still override the
paths for debugging, but the script owns and removes those files during cleanup.

Optional integrations use environment variables such as:

```bash
OURA_API_TOKEN=
HEALTH_SYNC_TOKEN=
FITNESS_DASHBOARD_PUBLIC_BASE_URL=
APPLE_HEALTH_WEBHOOK_URL=
WHOOP_CLIENT_ID_FILE=
WHOOP_SCOPES=
WHOOP_PROTECTED_MATERIAL_DIR=
OW_BASE_URL=
OW_USERNAME=
OW_PASSWORD=
OW_USER_ID=
OW_PORTAL_URL=
OW_SIDECAR_ENV_PATH=
OW_ALLOWED_HOSTS=
SECRET_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_ID=
LM_STUDIO_URL=
LM_STUDIO_MODEL=
LM_STUDIO_PRIMARY_URL=
LM_STUDIO_PRIMARY_MODEL=
LM_STUDIO_FALLBACK_URL=
LM_STUDIO_FALLBACK_MODEL=
LM_STUDIO_TIMEOUT_SEC=
LM_STUDIO_PREFLIGHT_TIMEOUT_SEC=
VISION_ESTIMATOR_PROVIDER=lm_studio
# Primary vision route, normally the ASUS GX10 LM Studio instance.
VISION_LM_STUDIO_URL=
VISION_LM_STUDIO_MODEL=
# Optional lower-memory model on the primary vision route. Set this explicitly
# to a host-verified smaller VLM; there is no safe 30B default for contended hosts.
VISION_LM_STUDIO_LOW_MEMORY_MODEL=
# Fallback vision route, normally the Mac Studio LM Studio instance.
VISION_LM_STUDIO_FALLBACK_URL=
VISION_LM_STUDIO_FALLBACK_MODEL=
VISION_LOCAL_TIMEOUT_SEC=
VISION_LM_STUDIO_PREFLIGHT_TIMEOUT_SEC=
OLLAMA_URL=
VISION_OLLAMA_MODEL=
```

Do not commit real values for these variables.
Production must set `SECRET_KEY` via environment or a secret manager; `.flask-secret` is local-dev only and is excluded from images and bundles.

For Apple Health / Health Auto Export setup, keep `HEALTH_SYNC_TOKEN` secret and set one public URL source for setup URL generation:

```bash
FITNESS_DASHBOARD_PUBLIC_BASE_URL=https://<your-public-fitness-dashboard-host>
```

The owner-only setup route returns the webhook URL and the request header to configure:

```text
URL: ${FITNESS_DASHBOARD_PUBLIC_BASE_URL}/api/apple-health/sync
Header: X-Sync-Token: <HEALTH_SYNC_TOKEN>
```

Use `APPLE_HEALTH_WEBHOOK_URL` only when the sync endpoint is intentionally exposed at a different public path. Keep the token out of the URL.

WHOOP setup is split between public client configuration and protected token
material:

- `WHOOP_CLIENT_ID_FILE` points at a local file containing only the WHOOP client
  ID. If unset, the app looks for `.whoop-client-id` at the repo root.
- The WHOOP client secret is read from macOS Keychain service
  `fitness-dashboard-whoop-client-secret`.
- OAuth access/refresh material is stored through the protected WHOOP material
  store, not in backups or normal API responses. `WHOOP_PROTECTED_MATERIAL_DIR`
  can point that protected store outside the repo.
- Manual WHOOP CSV import is available through Settings when OAuth is not
  configured. CSV input is size/row capped, UTF-8 validated, normalized into
  daily facts, and treated as untrusted input.

Open Wearables setup is exposed through Settings as a non-technical wearable
wrapper. The app can prepare the local hub profile from the local Open Wearables
sidecar env, list provider actions, start cloud provider sign-in only when that
provider has real connector credentials, and create phone-app invitation codes
for SDK-style sources such as Apple Health, Samsung Health, and Google Health
Connect. Advanced values such as hub URL, username, secret, and mapped user id
stay behind diagnostics instead of being the normal setup path.

Open Wearables has separate metadata-check and durable-sync contracts:

- `POST /api/open-wearables/check-sync` fetches redacted source metadata and
  counts without writing wearable facts. `POST /api/health/sync` is the
  compatibility path for the same metadata-only behavior.
- `POST /api/open-wearables/sync` normalizes and durably writes recommendation
  facts, then returns source metadata, counts, and `facts_upserted`. Dashboard
  sync buttons use this durable route.

Neither route returns raw health payloads. Durable WHOOP OAuth sync remains a
separate provider-specific flow.

Browser-initiated state-changing requests send `X-Requested-With: XMLHttpRequest`; the server also accepts browser same-origin metadata for cached app-shell rollouts and rejects mismatched browser origins before checking the CSRF header. The token-authenticated Apple Health sync endpoint and Stripe's signed webhook are explicitly exempt because they are called by external systems rather than the dashboard UI.

## Git Workflow

`main` is the stable branch. Updates should go through focused branches and pull requests.

Use branch names like:

```text
codex/docs-photo-food-logging
codex/feature-apple-health-sync
codex/fix-workout-execution
codex/qa-mobile-active-workout
```

Before pushing code, check that no private runtime data is included.
