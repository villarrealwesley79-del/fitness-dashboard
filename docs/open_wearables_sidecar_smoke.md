# Open Wearables live sidecar smoke check

Use this owner-run check after the local Open Wearables sidecar and Fitness
Dashboard are running and the dashboard is already mapped to the hub user. It
exercises the real dashboard-to-sidecar contract; it is not a unit fixture.

The command verifies:

- the live provider catalog;
- the dashboard setup check against the loopback sidecar URL;
- a metadata sync through the configured hub user;
- one provider that is correctly blocked; and
- one provider that reaches the ready authorization path.

The metadata sync is a real sync and can update the dashboard's normal wearable
facts. The smoke report itself never retains raw responses, health values,
credentials, session cookies, messages, or authorization URLs. It records only
status, stable error codes, provider identifiers, provider count, and the names
of returned count fields.

## Run it

Copy the authenticated owner's `Cookie` request header from the browser into a
temporary owner-readable file. Keep the literal `session=...` value out of shell
history and command arguments.

```bash
umask 077
${EDITOR:-vi} /tmp/fitness-dashboard-smoke.cookie
chmod 600 /tmp/fitness-dashboard-smoke.cookie

venv/bin/python scripts/smoke_open_wearables_sidecar.py \
  --dashboard-url http://127.0.0.1:5000 \
  --sidecar-url http://localhost:8000 \
  --cookie-file /tmp/fitness-dashboard-smoke.cookie \
  --output /tmp/open-wearables-smoke.json
```

The sidecar URL must exactly match the dashboard's configured base URL and use
`localhost`, `127.0.0.1`, or `::1`. Do not substitute one loopback spelling for
another: IPv4 and IPv6 loopback listeners can be different processes. The
cookie file must be a regular file with no group or world permissions. Remove
the temporary cookie file after the run. Exit status is `0` for a complete pass
and `2` for an explicit blocked result.

## Stable unavailable and blocked codes

The smoke command emits these command-level codes without exception text:

- `smoke_dashboard_unavailable`: the dashboard request could not complete;
- `smoke_auth_required`: the session cookie is absent, expired, or invalid;
- `smoke_owner_access_required`: the session is not the configured owner;
- `smoke_csrf_required`: the dashboard rejected the required same-origin API
  request marker;
- `smoke_invalid_response`: a dashboard route did not return the JSON contract;
- `smoke_redacted_error`: a drifted response supplied an unrecognized error
  value, which was intentionally not retained;
- `smoke_redirect_refused`: a dashboard route attempted any redirect, which is
  rejected so the owner cookie cannot be forwarded;
- `smoke_dashboard_insecure`: a non-loopback dashboard URL used plain HTTP;
- `smoke_provider_paths_missing`: the live catalog has no blocked/ready pair;
- `smoke_provider_catalog_failed`, `smoke_setup_check_failed`,
  `smoke_metadata_sync_failed`, `smoke_blocked_provider_failed`, or
  `smoke_ready_provider_failed`: the named live contract did not pass;
- `smoke_cookie_file_missing`, `smoke_cookie_file_unreadable`,
  `smoke_cookie_file_invalid`, or `smoke_cookie_file_unsafe`: the session-cookie
  input failed its local safety checks; and
- `smoke_sidecar_not_local` or `smoke_invalid_url`: an endpoint failed the
  command's URL boundary.
- `sidecar_mismatch`: the setup response's configured base URL did not exactly
  match the requested local sidecar URL.

The dashboard may retain these existing connector codes in the safe summary:

- `provider_catalog_unavailable`: the sidecar catalog is missing or unreachable;
- `provider_not_ready`, `provider_disabled`, or `provider_app_needed`: a cloud
  connector is present but not ready for authorization;
- `hub_restart_needed`: managed connector settings require a sidecar restart;
- `sdk_provider`: the source pairs through the phone health-store flow;
- `missing_user_mapping`: the dashboard owner is not mapped to a hub user; and
- `open_wearables_sync_failed`: live metadata sync failed without exposing the
  upstream exception or health payload.
- `open_wearables_auth_error`: the sidecar rejected its configured hub
  authentication;
- `open_wearables_config_error`: the sidecar could not use its connector
  configuration; and
- `open_wearables_sync_error`: at least one allowlisted live metadata endpoint
  failed without exposing its upstream response.

Any blocked result is a failed smoke run. Fix the reported stable code, rerun,
and keep only the redacted JSON summary if evidence is needed.
