# Fitness Dashboard

Fitness Dashboard is a local-first fitness and recovery app for planning workouts, tracking training execution, and turning wearable/health signals into practical daily guidance.

The product direction is to become a trusted daily fitness operating system: the user should know what to train, how hard to train, what recovery signals matter, and how food choices affect the rest of the day.

## What It Does

- Plans and adjusts strength workouts based on readiness, soreness, recent volume, and user constraints.
- Tracks active workouts, set completion, swaps, notes, and workout history.
- Surfaces recovery context from Oura, Apple Health / Health Auto Export, and local cached data.
- Supports nutrition and body-composition tracking.
- Plans toward photo-based food logging, where a user can snap a picture of food and the app updates calorie/macronutrient context and daily coaching guidance.
- Keeps sensitive runtime data local by default.

## Current Repo Contents

This GitHub repo is a sanitized project copy. It intentionally does not include the old local git history because that history contained private runtime artifacts.

Included:

- Flask application source
- Templates and static assets
- Oura, Apple Health, nutrition, workout, and AI-coach integration code
- Deployment entry files
- Product planning docs in `docs/`

Excluded:

- `.env` files
- auth databases
- SQLite databases
- local health exports
- Oura/Apple Health caches
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
SECRET_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_ID=
LM_STUDIO_URL=
LM_STUDIO_MODEL=
VISION_ESTIMATOR_PROVIDER=lm_studio
VISION_LM_STUDIO_URL=
VISION_LM_STUDIO_MODEL=
OLLAMA_URL=
VISION_OLLAMA_MODEL=
```

Do not commit real values for these variables.

For Apple Health / Health Auto Export setup, keep `HEALTH_SYNC_TOKEN` secret and set one public URL source for setup URL generation:

```bash
FITNESS_DASHBOARD_PUBLIC_BASE_URL=https://<your-public-fitness-dashboard-host>
```

The owner-only setup route will emit:

```text
${FITNESS_DASHBOARD_PUBLIC_BASE_URL}/api/apple-health/sync?token=<HEALTH_SYNC_TOKEN>
```

Use `APPLE_HEALTH_WEBHOOK_URL` only when the sync endpoint is intentionally exposed at a different public path. The value should be the endpoint URL without the token; the setup route appends the token.

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
