# Release, Restart, and Rollback Runbook

Linear: FIT-19

## Scope

This runbook covers the current Flask/PWA deployment path for the local-first
Fitness Dashboard. It does not delete runtime data. WHOOP, Oura and Apple Health
reach the app only through Open Wearables.

## Release Checklist

1. Start from a focused branch linked to one Linear issue.
2. Confirm the diff does not include private runtime data:
   - `.env` or `.env.*`
   - `auth.db*`
   - `fitness_data.db`
   - `apple_health_sync.db`
   - `oura_daily.sqlite3`
   - `whoop.sqlite3`
   - `.whoop-client-id`
   - `.whoop-protected-material-*.json`
   - `whoop_sync.lock`
   - `ai_coach_cache.sqlite3`
   - `data_*.json`
   - logs, backup bundles, screenshots, and generated audit artifacts
3. Run the narrow checks for the files changed.
4. Run `git diff --check`.
5. For backend or route changes, run the authenticated smoke test:

```bash
BASE_URL=http://127.0.0.1:5050 \
FITNESS_SMOKE_USERNAME=<owner-username> \
FITNESS_SMOKE_PASSWORD=<owner-password> \
bash support/self_test.sh
```

Before release, run the dependency-free shell syntax gate:

```bash
scripts/check-shell-scripts.sh
```

The gate runs `bash -n` over the launchd installer, worktree guard and
installer, pre-push and post-checkout hooks, and the live self-test. It intentionally does not treat ShellCheck's semantic or style
warnings as release failures because ShellCheck is not a repository dependency
or a standard macOS tool. No `bash -n` syntax diagnostics are ignored.

6. Open a pull request with the Linear issue, tests, risk, and intentionally
   excluded work.

## Restart

Use the launchd service when running on the Mac mini:

```bash
launchctl kickstart -k gui/$(id -u)/com.fitness-dashboard
```

Then verify the process is listening and the auth gate still works:

```bash
lsof -nP -iTCP:5050 -sTCP:LISTEN
curl -i http://127.0.0.1:5050/
```

The root route should redirect to login when no session is present.

## Launchd Install / Migration

For a new Mac or a repaired checkout, install the user launchd agent with the
repo script:

```bash
bash scripts/install-launchd-agents.sh --dry-run
bash scripts/install-launchd-agents.sh install
```

The script writes `~/Library/LaunchAgents/com.fitness-dashboard.plist`, then
bootstraps and kickstarts it. It is idempotent; rerunning `install` only
rewrites the plist when its generated content changes. Use `reinstall` after
moving the repo path or changing Python environments, and use `uninstall` to
remove the agent. `--dry-run` prints the generated plist without writing files
or calling `launchctl`. The agent pins launchd to `HOST=127.0.0.1`, `PORT=5050`,
and `FLASK_DEBUG=0` so local development `.env` values do not leak into the
background service.

The Apple Health staleness agent (`com.fitness-dashboard.staleness`) no longer
exists. A machine that still has it loads it until it is removed by hand:

```bash
launchctl bootout gui/$(id -u)/com.fitness-dashboard.staleness 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.fitness-dashboard.staleness.plist
```

## Cache Bust

When static JavaScript or CSS changes behavior, update the asset version used by
the templates in the same PR. After restart, verify the browser loads the new
asset URL instead of a cached previous bundle.

For backend-only or docs-only releases, no asset version change is required.

## Deploy By Commit And Roll Back

Deploy a recorded commit that was tested first, never "whatever main is now".

1. Run the checks above on `<sha>`.
2. On the host: `git fetch origin`, confirm `git status --porcelain` is clean (data
   files are ignored), then `git checkout --detach <sha>`. Print `git rev-parse
   HEAD` into the deploy log and keep the previous SHA.
3. Refresh dependencies: `pip install -r requirements.txt`.
4. Restart: `launchctl kickstart -k gui/$(id -u)/com.fitness-dashboard`.
5. Verify the Open Wearables path:
   - the app log shows the startup banner and no `Traceback`;
   - `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5050/login` returns 200;
   - after signing in, the dashboard shows the `Sources:` line naming the
     providers, History lists workouts, and `/api/freshness` reports
     `open_wearables` as `fresh` and `wearable_facts` with a recent
     `last_data_point`;
   - `POST /api/open-wearables/check-sync` returns redacted metadata only and does
     not expose upstream payloads or exception text;
   - `POST /api/apple-health/sync` is rejected (it no longer exists);
   - Open Wearables provider pairing refuses cloud providers whose connector
     credentials are missing or placeholder values;
   - Open Wearables phone health sources create invitation codes through
     `/api/open-wearables/mobile-invite/<provider>` and return a phone-usable
     hub URL without exposing the hub secret.

Rollback: `git checkout --detach <previous sha>`, restart, and rerun the same
checks. Do not reset shared history and do not delete runtime SQLite databases
or JSON data files; no release changes a schema or deletes rows, and data written
after the deploy is kept.

## Wearable Data Sources

WHOOP, Oura and Apple Health arrive only through the Open Wearables hub and are
stored as normalized facts in `wearable_facts.sqlite3`. The old direct files
(`oura_daily.sqlite3`, `whoop.sqlite3`, `apple_health_sync.db`, `.whoop-*`,
`.health-sync-token`, `.apple-health-first-sync`) are no longer read; archive
them outside Git before removing them. The WHOOP client secret stays in the
Open Wearables sidecar env, never in this repo, docs, logs, screenshots, PR text
or Linear comments.

Open Wearables setup is app-first. The Settings flow should prepare the local
hub profile through `/api/open-wearables/setup/bootstrap` when the local sidecar
env is available. The default sidecar env path is:

```bash
~/open-wearables/backend/config/.env
```

Use `OW_SIDECAR_ENV_PATH` only when the Open Wearables checkout stores that file
elsewhere. If the sidecar env changes, restart the Open Wearables hub before
testing provider sign-in.

Cloud providers such as Oura, Garmin, Strava, Fitbit, Polar, Suunto, and
Ultrahuman must stay in owner-setup state until their Open Wearables connector
credentials are real. A broken provider OAuth page is a release failure, not an
acceptable setup screen. Apple Health, Samsung Health, and Google Health Connect
are phone health-source flows; verify that they create an Open Wearables
invitation code instead of opening a provider website.

Open Wearables remains a best-effort local bridge. Its `/api/open-wearables/check-sync`
and `/api/open-wearables/sync` responses are intentionally metadata-only. Treat
any change that returns raw Open Wearables data, hub secrets, token names, or raw
exception text as a privacy regression unless a future issue explicitly changes
the backup/export and security contract.
