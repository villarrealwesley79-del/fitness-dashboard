#!/bin/bash
# Dependency-free syntax validation for the operational shell entrypoints.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS=(
    "scripts/install-launchd-agents.sh"
    "scripts/check-apple-health-staleness.sh"
    "scripts/worktree-server-guard.sh"
    "scripts/install-worktree-guard.sh"
    ".githooks/pre-push"
    ".githooks/post-checkout"
    "support/self_test.sh"
)

for script in "${SCRIPTS[@]}"; do
    printf 'bash -n: %s\n' "${script}"
    bash -n "${ROOT}/${script}"
done

printf 'Shell static checks passed (%s files).\n' "${#SCRIPTS[@]}"
