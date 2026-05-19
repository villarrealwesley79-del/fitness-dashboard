# Repository Hygiene

Linear: FIT-19

## Artifact Policy

The GitHub repository is a sanitized source copy. Runtime data stays local and
must not be committed.

## Keep In Git

- Flask source, templates, static assets, support scripts, and tests.
- Product docs under `docs/`.
- Sanitized QA evidence when it is durable and referenced by a PR.
- Example commands that use placeholders instead of real hostnames, tokens, or
  personal paths.

## Keep Local And Ignored

- `.env` and `.env.*`
- `.flask-secret`
- `.health-sync-token`
- `.apple-health-first-sync`
- `auth.db*`
- `fitness_data.db`
- `apple_health_sync.db`
- `oura_daily.sqlite3`
- `ai_coach_cache.sqlite3`
- `data_*.json`
- logs, backup bundles, generated smoke-test bodies, visual-review screenshots,
  and temporary audit artifacts

## Archive Or Ignore

Generated visual review folders, audit bundles, local backup files, and one-off
proof JSON files can be kept outside Git for debugging. They should not be used
as PR acceptance evidence unless copied into a durable sanitized docs path.

Root-level stale app files such as old `index.html`, `app.js`, `dashboard.js`,
or `style.css` are not the production UI source when the real app uses
`templates/` and `static/`. Treat those files as local artifacts unless a future
issue explicitly adopts or removes them.

## Remove Only With Explicit Approval

Do not delete runtime databases, local JSON data, auth state, or generated
health exports during cleanup work. Removal needs an explicit issue and a
backup/restore note because these files may contain the owner's live history.

## Stale Docs Rule

Docs should describe the current Apple Health architecture as token-gated Health
Auto Export or Shortcuts-style webhook sync. Any older instruction that implies
a public untokened webhook, a hardcoded personal Tailscale hostname, or browser
direct HealthKit access should be updated rather than copied forward.

## Current Ignored Coverage

The repo already ignores the main private/runtime classes:

- environment files and local secrets
- SQLite databases
- `data_*.json`
- logs and backup/temp files
- visual review and audit bundle outputs

When adding a new generated artifact path, update `.gitignore` in the same issue
only if the path can be described generically without hiding source files.
