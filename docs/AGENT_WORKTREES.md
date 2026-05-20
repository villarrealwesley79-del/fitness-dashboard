# Agent Worktrees

Linear: FIT-85

Use one branch and one worktree per Linear issue. Do not switch branches in a
worktree while the Flask dashboard is running from that same directory; that can
leave in-memory routes from one branch serving templates, auth settings, or data
contracts from another branch.

## Branch Switch Guard

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
