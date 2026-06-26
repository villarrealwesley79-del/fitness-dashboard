# Release, Restart, and Rollback Runbook

Linear: FIT-19

## Scope

This runbook covers the current Flask/PWA deployment path for the local-first
Fitness Dashboard. It does not delete runtime data and does not replace the
token-gated Health Auto Export bridge.

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

For a new Mac or a repaired checkout, install both user launchd agents with the
repo script:

```bash
bash scripts/install-launchd-agents.sh --dry-run
bash scripts/install-launchd-agents.sh install
```

The script writes `~/Library/LaunchAgents/com.fitness-dashboard.plist` and
`~/Library/LaunchAgents/com.fitness-dashboard.staleness.plist`, then bootstraps
and kickstarts them. It is idempotent; rerunning `install` only rewrites plist
files when their generated content changes. Use `reinstall` after moving the repo
path or changing Python environments, and use `uninstall` to remove both agents.
`--dry-run` prints the generated plists without writing files or calling
`launchctl`. The dashboard agent pins launchd to `HOST=127.0.0.1`, `PORT=5050`,
and `FLASK_DEBUG=0` so local development `.env` values do not leak into the
background service. The staleness agent is pointed at the selected repo's
`apple_health_sync.db`, so migrated checkouts do not keep monitoring an old path.

## Cache Bust

When static JavaScript or CSS changes behavior, update the asset version used by
the templates in the same PR. After restart, verify the browser loads the new
asset URL instead of a cached previous bundle.

For backend-only or docs-only releases, no asset version change is required.

## Rollback

1. Identify the last known-good commit or merged pull request.
2. Create a new rollback branch from `origin/main`.
3. Revert the bad commit with `git revert <commit>` rather than resetting shared
   history.
4. Do not delete runtime SQLite databases or JSON data files as part of rollback.
5. Restart the service and rerun the same checks used for the original change.
6. If Apple Health sync was affected, verify:
   - `/api/apple-health/sync` rejects missing or invalid tokens.
   - `/api/apple-health/sync/status` remains auth-gated.
   - `/api/apple-health/sync/setup-url` still emits the configured public base
     URL only through the owner-auth setup route.
7. If WHOOP or Open Wearables sync was affected, verify:
   - WHOOP OAuth start reports `missing_whoop_config` when local client config
     is absent.
   - WHOOP sync/import/delete/disconnect routes are auth and browser-mutation
     gated.
   - WHOOP backup export contains normalized `whoop_daily_facts` only, not token
     material or raw provider payloads.
   - `/api/health/sync` returns redacted Open Wearables metadata only and does
     not expose upstream payloads or exception text.

## Apple Health Bridge

Apple Health remains a Health Auto Export or Shortcuts-style bridge into the
backend. The public URL comes from:

```bash
FITNESS_DASHBOARD_PUBLIC_BASE_URL=https://<your-public-fitness-dashboard-host>
```

Use `APPLE_HEALTH_WEBHOOK_URL` only for an intentionally different public sync
endpoint. Do not hardcode a personal Tailscale host in committed docs or config.
The setup route appends the configured `HEALTH_SYNC_TOKEN` to the endpoint.

## WHOOP And Open Wearables

WHOOP is the durable local-first source for WHOOP recovery-aware behavior. Keep
the client ID in a local `.whoop-client-id` file or `WHOOP_CLIENT_ID_FILE`, keep
the client secret in macOS Keychain service
`fitness-dashboard-whoop-client-secret`, and keep access/refresh material in the
protected material store. Do not place those values in the repo, docs, logs,
screenshots, PR text, or Linear comments.

Open Wearables remains a best-effort local bridge. Its `/api/health/sync`
response is intentionally metadata-only. Treat any change that returns raw Open
Wearables data as a privacy regression unless a future issue explicitly changes
the backup/export and security contract.
