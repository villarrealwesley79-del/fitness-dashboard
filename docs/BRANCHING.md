# Fitness Dashboard Branching Workflow

## Branch Rules

`main` is the stable branch. It should only contain reviewed, intentional updates.

All future work should happen on short-lived branches with this shape:

```text
codex/<area>-<change>
```

Examples:

```text
codex/photo-food-logging-prd
codex/workout-execution-roadmap
codex/integration-confidence-docs
codex/product-hardening-plan
```

## Update Flow

1. Start from the latest `main`.
2. Create a focused branch for one update.
3. Change only the files needed for that update.
4. Commit with a plain description of the change.
5. Push the branch to GitHub.
6. Open a draft pull request.
7. Merge only after the diff is reviewed and no private data is included.

## Scope Rules

Keep product docs, roadmap updates, and implementation work separate when possible.

Use these branch prefixes:

```text
codex/docs-...
codex/roadmap-...
codex/feature-...
codex/fix-...
codex/qa-...
```

## Privacy Rule

Do not push local databases, tokens, health exports, secrets, `.env` files, auth files, cache files, generated logs, or user-specific data.

The original local app checkout contains private runtime data, so GitHub updates should be staged intentionally from a clean branch or clean export.

## Current Repo Intent

This GitHub repo currently holds the safe product planning layer for the Fitness Dashboard:

```text
docs/VISION.md
docs/PRD.md
docs/CURRENT_STATE.md
docs/BRANCHING.md
```

Implementation code can be added later only after it is cleaned of private runtime data.
