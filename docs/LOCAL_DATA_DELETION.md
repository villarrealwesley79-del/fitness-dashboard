# Local data deletion boundaries

Fitness Dashboard has two different deletion scopes. They are not
interchangeable, and the product must not describe the first as deleting an
account or all local data.

## Structured user-row deletion

`data_store.delete_user_data(user_id)` is an internal, currently unwired helper.
It deletes rows for one `user_id` from the documented tables inside
`fitness_data.db`. It keeps the SQLite file and does not delete authentication,
JSON history, wearable databases, provider tokens, configuration, locks,
in-memory caches, or browser/offline state.

Run the read-only inventory against the exact loaded service `DATA_DIR` to see
the current table list and excluded stores:

```bash
launchctl print gui/$(id -u)/com.fitness-dashboard
venv/bin/python support/local_data_deletion_inventory.py \
  --data-dir /exact/DATA_DIR \
  --app-dir /exact/loaded/application/directory \
  --working-directory /exact/loaded/WorkingDirectory \
  --service-environment-json /owner-only/loaded-environment.json \
  --service-environment-reviewed \
  --open-wearables-sidecar-env-path /exact/open-wearables/.env \
  --apple-health-sync-db /exact/override/path
```

Pass the loaded service's exact application directory, working directory, and
a protected JSON object containing the loaded environment. The JSON is read
only to derive paths and configured/unconfigured status; values are never
printed. Every active service override may also be repeated with the
corresponding `--help` option as a consistency check;
omit an option only after confirming that variable is unset in the loaded job.
Use `--credential-configured NAME` once for every credential-bearing variable
reported by `--help`; `--health-sync-token-configured` remains a shorthand for
the Apple Health token. Secret values are never accepted or printed. The command refuses to run
without `--service-environment-reviewed`. It is dry-run-only. It may read
`open_wearables_config.json` to resolve the sidecar path, but it does not read
data/secret contents or create, edit, or delete any listed path.

## Full local purge

There is no full-purge API or button. A full purge is an owner-operated recovery
procedure because data spans the service data directory, app-directory auth
secret, macOS Keychain/protected fallback files, optional environment override
paths, and browser storage.

Before removing anything:

1. Read the loaded launchd job and record the exact `DATA_DIR` and any Apple
   Health, WHOOP, or Open Wearables path overrides.
2. Create and verify a backup if any history may need to survive.
3. Use the product's provider disconnect flows first so WHOOP and Open
   Wearables protected credentials are removed from Keychain or their protected
   fallback files. Separately remove and verify the
   `fitness-dashboard-whoop-client-secret` Keychain credential; disconnect does
   not remove this application credential. Deleting only a SQLite/config file
   is not token revocation.
4. Stop the dashboard and health-sync writers. Do not remove live SQLite files
   or lock files while a writer is running.
5. Run the inventory command and inspect every resolved path, its `present`
   status, and any protected-material glob matches. Delete files
   individually only after confirming they belong to this exact runtime; never
   use a recursive repository-wide deletion command.
6. Include the JSON histories, app-generated `*.json.corrupt-*.json`
   recovery copies, SQLite stores and their `-wal`/`-shm`/`-journal` sidecars,
   auth database, provider config,
   AI cache, sync markers/locks, protected fallback material, and the
   app-directory `.env`, the resolved Open Wearables sidecar `.env`, their
   `.before-managed-connectors-*` credential backups, `.flask-secret`, and
   other secret paths listed by the inventory. These env files and backups can
   retain provider credentials; if the owner chooses to
   retain it, do not describe the result as a full local purge. Check each
   configured override path separately.
7. In each browser profile used with the app, clear the listed localStorage and
   sessionStorage keys, clear the `fitMealIntakeQueueDB` IndexedDB database,
   and unsubscribe/verify absent the active service-worker PushManager
   subscription. Server-file removal does not clear queued photos, drafts,
   adjustment intent, push registration, or active-workout state in browsers.
8. Remove every listed credential variable from the loaded service
   configuration, reload the service definition, and confirm
   `configuration_ready_for_full_purge` is `true`, not `null`, and
   `inventory_errors` is
   empty. Matching runtime precedence, the Open Wearables sidecar path resolves
   from `open_wearables_config.json`, then the explicit service override, then
   its documented default.
   If any credential is
   intentionally retained, do not call the result a full local purge. Rerun
   the inventory. Restart only after every expected concrete path reports
   `present: false`, protected-material glob matches are empty, and
   provider protected material has been verified absent. The next launch is a
   first-run setup and requires a new local account.

Backups, exported health payloads, screenshots, logs, and files outside the
resolved runtime paths are not automatically covered. Decide their retention
separately; do not claim a full purge while copies remain.

## Copy contract

Use “Delete structured user rows” only for the internal `fitness_data.db`
operation. Reserve “Delete all local data” or “Delete account” for a future
flow that proves every server, protected-secret, override, and browser store in
the dry-run inventory was handled.
