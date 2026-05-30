#!/bin/bash
# Detect a Flask dashboard server running from this git worktree.

set -u

MODE="check"
ROOT=""

usage() {
    cat <<'USAGE'
Usage: scripts/worktree-server-guard.sh [--root PATH] [--quiet]

Exits 1 when a python dashboard server process is running from the given worktree.
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            shift
            ROOT="${1:?missing --root value}"
            ;;
        --quiet)
            MODE="quiet"
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

if [ -z "${ROOT}" ]; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
ROOT="$(cd "${ROOT}" && pwd -P)"

PGREP="$(command -v pgrep || true)"
LSOF="$(command -v lsof || true)"
[ -n "${LSOF}" ] || [ ! -x /usr/sbin/lsof ] || LSOF="/usr/sbin/lsof"

if [ -z "${PGREP}" ] || [ -z "${LSOF}" ]; then
    [ "${MODE}" = "quiet" ] || echo "worktree-server-guard: pgrep and lsof are required" >&2
    exit 2
fi

FOUND=""
PIDS="$("${PGREP}" -f '[p]ython' 2>/dev/null || true)"
for pid in ${PIDS}; do
    [ "${pid}" = "$$" ] && continue
    cwd="$("${LSOF}" -a -p "${pid}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    cmd="$(ps -ww -p "${pid}" -o command= 2>/dev/null || ps -p "${pid}" -o command= 2>/dev/null || true)"
    case "${cmd}" in
        *app.py*|*serve_fit*.py*|*docs/qa/*serve*.py*)
            ;;
        *)
            continue
            ;;
    esac
    case "${cwd}" in
        "${ROOT}"|"${ROOT}/"*)
            FOUND="${FOUND}${pid} "
            continue
            ;;
    esac
    case "${cmd}" in
        *"${ROOT}/app.py"*|*"${ROOT}/docs/qa/"*serve*.py*)
            FOUND="${FOUND}${pid} "
            ;;
    esac
done

FOUND="$(printf '%s' "${FOUND}" | sed 's/[[:space:]]*$//')"
if [ -n "${FOUND}" ]; then
    if [ "${MODE}" != "quiet" ]; then
        echo "Flask server detected on PID ${FOUND} running from this worktree." >&2
        echo "Stop it before switching branches, or use 'git worktree add' for the new branch." >&2
    fi
    exit 1
fi

exit 0
