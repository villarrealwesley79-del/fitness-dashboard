#!/bin/bash
# Install the worktree server guard into git metadata so it survives checkout.

set -euo pipefail

DRY_RUN=0

usage() {
    cat <<'USAGE'
Usage: scripts/install-worktree-guard.sh [--dry-run]

Copies the branch-switch guard into this worktree's git metadata directory and
sets this worktree's core.hooksPath to that stable path. This keeps the guard
active even when checking out branches that do not contain the committed
.githooks directory, without changing sibling worktrees.
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-path fitness-worktree-guard-hooks)"
HOOKS_PATH="$(cd "$(dirname "${HOOK_DIR}")" && pwd -P)/$(basename "${HOOK_DIR}")"

run() {
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

if [ "${DRY_RUN}" = "1" ]; then
    printf '[dry-run] mkdir -p %s\n' "${HOOKS_PATH}"
    printf '[dry-run] install %s %s\n' "${ROOT}/.githooks/post-checkout" "${HOOKS_PATH}/post-checkout"
    printf '[dry-run] install %s %s\n' "${ROOT}/scripts/worktree-server-guard.sh" "${HOOKS_PATH}/worktree-server-guard.sh"
    printf '[dry-run] git config extensions.worktreeConfig true\n'
    printf '[dry-run] git config --worktree core.hooksPath %s\n' "${HOOKS_PATH}"
    exit 0
fi

mkdir -p "${HOOKS_PATH}"
install -m 0755 "${ROOT}/.githooks/post-checkout" "${HOOKS_PATH}/post-checkout"
install -m 0755 "${ROOT}/scripts/worktree-server-guard.sh" "${HOOKS_PATH}/worktree-server-guard.sh"
git config extensions.worktreeConfig true
git config --worktree core.hooksPath "${HOOKS_PATH}"
printf 'Installed worktree guard at %s\n' "${HOOKS_PATH}"
