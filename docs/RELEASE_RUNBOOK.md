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

## Stripe Webhook Event History

The Stripe blueprint is intentionally dormant and is not registered by the
current application. This audit facility applies to direct blueprint integration
tests and to a future deployment that explicitly registers that blueprint; the
current production app does not serve `/webhook` or create this database.

Verified Stripe webhook deliveries are recorded in
the `stripe_webhook_events` table in `$DATA_DIR/auth.db`. Keeping the audit row
and entitlement assignment in the same SQLite transaction makes retries safe
after process interruption. The audit table stores only the Stripe
event ID, event type, Stripe event timestamp, local receipt/completion
timestamps, and processing status. It does not store webhook signatures or raw
payloads.

Inspect recent processing state on the host without copying the database into
the repository:

```bash
sqlite3 "$DATA_DIR/auth.db" \
  "SELECT event_id, event_type, event_created_at, status, received_at, processed_at FROM stripe_webhook_events ORDER BY received_at DESC LIMIT 25;"
```

`processing` exists only inside the transaction. `processed` means the local
entitlement assignment and audit marker committed together. `failed` means the
transaction rolled back and Stripe should retry the delivery; a later successful
retry updates the same event row.

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
   - Open Wearables provider pairing refuses cloud providers whose connector
     credentials are missing or placeholder values.
   - Open Wearables phone health sources create invitation codes through
     `/api/open-wearables/mobile-invite/<provider>` and return a phone-usable
     hub URL without exposing the hub secret.

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

Open Wearables setup is app-first. The Settings flow should prepare the local
hub profile through `/api/open-wearables/setup/bootstrap` when the local sidecar
env is available. The default sidecar env path is:

```bash
~/open-wearables/backend/config/.env
```

Use `OW_SIDECAR_ENV_PATH` only when the Open Wearables checkout stores that file
elsewhere. The web app may copy local WHOOP connector credentials into the
Open Wearables sidecar env when they are available and the Open Wearables env
still has placeholder values. If that changes the sidecar env, restart the Open
Wearables hub before testing provider sign-in.

Cloud providers such as Oura, Garmin, Strava, Fitbit, Polar, Suunto, and
Ultrahuman must stay in owner-setup state until their Open Wearables connector
credentials are real. A broken provider OAuth page is a release failure, not an
acceptable setup screen. Apple Health, Samsung Health, and Google Health Connect
are phone health-source flows; verify that they create an Open Wearables
invitation code instead of opening a provider website.

Open Wearables remains a best-effort local bridge. Its `/api/health/sync`
and `/api/open-wearables/sync` responses are intentionally metadata-only. Treat
any change that returns raw Open Wearables data, hub secrets, token names, or raw
exception text as a privacy regression unless a future issue explicitly changes
the backup/export and security contract.
