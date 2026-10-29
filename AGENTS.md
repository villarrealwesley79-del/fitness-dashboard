# Repository instructions

Private, single-owner fitness app (Flask). Scope: workout plan and logging, sleep and recovery, body and cardio, Open Wearables as the only wearable hub. See `README.md`.

Visual system (colours, type, spacing): see DESIGN.md.

@DESIGN.md

## Work

- The current Linear issue description is canonical. If it and a doc disagree, the issue wins.
- One scoped PR per change, from a branch off `origin/main`. Draft PR first; the title starts with the issue key.
- No software-factory, Slack, label or scanner gates. A PR needs only the checks below and the owner's review.

## Checks

Run the suite in a clean checkout or worktree, never in one that holds real data, with scratch directories:

```bash
DATA_DIR=$(mktemp -d) TMPDIR=$(mktemp -d) python -m pytest -q
```

- UI changes need browser proof: screenshots at phone and desktop widths, with synthetic data.
- Fixtures and screenshots use synthetic data only. Never commit real health data, credentials or databases.

## Owner-only gates

Ask the owner first, and do not do these on your own: credentials and secrets, permissions and access, spending money, destructive or irreversible actions (deleting data, force-push), and anything that touches the running app, deploys, launchd, Tailscale or the live host.

## Product rules

- The coach is deterministic. Workout and recovery guidance comes from rules over stored data; do not replace it with, or gate it on, a model call.
- Data trust: show only what the data supports. Stale, missing or invalid inputs are excluded or labeled as such, never filled in. Freshness and errors are reported truthfully.
- Keep the source of every recovery signal traceable (provider, observed time, freshness).

## Private runtime

- The dashboard is tailnet-only (or localhost). The host process binds 127.0.0.1 or the Tailscale IP, never `0.0.0.0` on the host, and never put it behind Funnel or another public proxy.
- The MCP endpoint is the only planned public surface, and it must sit behind login.
- Secrets come from the environment or protected stores, and are never printed, logged or committed.
