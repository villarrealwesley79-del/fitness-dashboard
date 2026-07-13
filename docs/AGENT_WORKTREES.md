# Agent Worktrees

Linear: FIT-36, FIT-85

Use one branch and one worktree per Linear issue. New feature, fix, and docs
branches start from `origin/main`; do not branch from local `master`.

FIT-36 is the canonical reconciliation writeup for the old unrelated local
`master` history. If a local `master` branch exists, treat it as a deprecated
alias kept only for reconciliation checks. Do not commit to it, do not branch
from it, and do not use it as the source of truth.

Do not switch branches in a worktree while the Flask dashboard is running from
that same directory; that can leave in-memory routes from one branch serving
templates, auth settings, or data contracts from another branch. FIT-43 is the
incident that exposed this failure mode.

## Branch Switch Guard

FIT-85 shipped the worktree/server safety guard that enforces the FIT-36 rule.

Enable the guard in each checkout:

```bash
scripts/install-worktree-guard.sh
```

The installer copies the committed hook and detector into this worktree's Git
metadata directory, then sets this worktree's `core.hooksPath` to that stable
path. That keeps the guard active even when checking out branches that do not
yet contain these guard files, without changing sibling worktrees.

The guard runs after `git checkout` or `git switch` attempts. If it detects a
`python app.py` dashboard process, or a repo QA launcher such as
`docs/qa/*/serve_*.py`, whose working directory is the current worktree, it
restores the previous branch and exits non-zero with instructions to stop the
server or create a fresh worktree.

Check the current worktree manually with:

```bash
scripts/worktree-server-guard.sh
```

The guard does not kill processes and does not restart the dashboard. Stop the
server yourself, or create another worktree:

```bash
git worktree add ../fitness-dashboard.fit-123 -b villarrealwesley79/fit-123-name origin/main
```

## Isolated Factory Previews

The factory boot command sets `FITNESS_DASHBOARD_FACTORY_PREVIEW=1` only for an
isolated, Tailnet-only preview. That flag disables Secure cookies for the HTTP
preview URL and seeds the preview database with this disposable owner-equivalent
account:

- Username: `test`
- Password: `1224`

Use this account for factory browser checks and owner acceptance. Each preview
must retain its own isolated data directory. Never set the factory-preview flag
for production or an ordinary local boot; `SESSION_COOKIE_SECURE=false` alone
does not seed the account.
