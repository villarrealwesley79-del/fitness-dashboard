# FIT-240 WHOOP Intake UX Design

## Role

Add the missing user-facing path for getting WHOOP data into Fitness Dashboard after FIT-239 shipped the durable backend.

## Scope

FIT-240 changes the Settings WHOOP row and supporting frontend code. It does not change WHOOP recommendation policy, token storage, sync persistence, or provider semantics unless a small backend response shape is required by the UI.

## Output Style

The Settings UI stays dense and operational. The WHOOP row should remain part of the existing Data sources card, with one primary action and compact secondary controls. Copy must explain the real state rather than describe how the feature works in marketing terms.

## Process Rules

- Use a clean issue worktree from `origin/main` on branch `codex/fit-240-whoop-intake`.
- Keep existing local user data out of the branch.
- Preserve the merged FIT-239 backend contracts:
  - `POST /api/whoop/connect/start` returns `authorization_url`.
  - `POST /api/whoop/import-csv` accepts JSON `{ "csv": "..." }` and multipart uploads.
- Manual import must work when OAuth config is missing.
- Live OAuth must not expose OAuth codes, states, tokens, or raw health data in committed files, screenshots, PR text, or Linear comments.

## Design

Clicking `Connect` opens a WHOOP intake modal. The modal has two sections:

1. `Connect live WHOOP`
   - Fetches `/api/whoop/connect/start`.
   - Attempts to open the authorization URL in a popup/new tab from the user action.
   - Shows a visible fallback link when popup opening fails or the user wants to open it manually.
   - If OAuth config is missing, shows an inline unavailable state and keeps the manual import section usable.

2. `Import WHOOP export`
   - Shows a textarea for pasted CSV/export data.
   - Shows a hidden file input with a compact `Choose file` button.
   - Submits pasted text or selected file to `/api/whoop/import-csv`.
   - On success, clears transient errors, refreshes WHOOP status/freshness, nulls dashboard/recommendation caches, and renders `WHOOP · CSV only` where applicable.
   - On validation error, shows the server error message inline and does not mutate the visible status.

OAuth callback already redirects to `/#settings`; the hash route should leave the user on Settings, and Settings should refresh WHOOP status after page load. No popup polling is required in this PR because real WHOOP OAuth credentials are not available locally.

## Quality Rules

- Desktop and mobile browser proof must cover: modal open, popup/link fallback, missing-config state, paste import success, paste validation error, CSV-only state, source modal remains usable, and existing Sync/Disconnect/Delete controls remain coherent.
- Tests must cover the frontend contract and backend CSV import path already used by the new UI.
- Final closeout must include PR evidence, artifact safety, autoreview/codex-review clean result, GitHub mergeability, Linear comment, and live local server restart with `DATA_DIR=/Users/admin/fitness-dashboard` preserved.
