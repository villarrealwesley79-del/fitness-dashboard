#!/bin/bash
# Apple Health sync staleness check.
# Exits non-zero (and logs a warning) if Health Auto Export has ever posted to
# /api/apple-health/sync but the last post was more than 36 hours ago.
# Before the first successful sync, we stay quiet on purpose so the iPhone-side
# HAE setup doesn't trigger warnings while it's being built.

set -u

LOG_DIR="/tmp"
LOG_FILE="${LOG_DIR}/apple-health-staleness.log"
FIRST_SEEN_FILE="${HOME}/.openclaw/workspace/projects/fitness-dashboard/.apple-health-first-sync"
STALE_AFTER_HOURS="${STALE_AFTER_HOURS:-36}"
DB_FILE="${HOME}/.openclaw/workspace/projects/fitness-dashboard/apple_health_sync.db"

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(timestamp)] $*" >> "${LOG_FILE}"; }

# The status endpoint is intentionally auth-gated, so this launchd watchdog
# reads the local SQLite database instead of storing web credentials.
if [ ! -f "${DB_FILE}" ]; then
    log "INFO: sync database not found at ${DB_FILE}; skipping staleness check"
    exit 0
fi

if ! LAST_EVENT=$(sqlite3 "${DB_FILE}" "SELECT MAX(created_at) FROM ah_sync_events;" 2>>"${LOG_FILE}"); then
    log "ERROR: failed to query ah_sync_events in ${DB_FILE}"
    exit 4
fi
if ! LAST_INSERT=$(sqlite3 "${DB_FILE}" "SELECT MAX(created_at) FROM ah_sync_log;" 2>>"${LOG_FILE}"); then
    log "ERROR: failed to query ah_sync_log in ${DB_FILE}"
    exit 4
fi
LAST="${LAST_EVENT:-${LAST_INSERT}}"

if [ -z "${LAST}" ]; then
    # Never synced. Stay quiet until the user configures the Shortcut.
    log "INFO: no sync has ever been recorded; skipping staleness check"
    exit 0
fi

# Remember the first time we ever saw a sync, so we don't alert during the quiet window
if [ ! -f "${FIRST_SEEN_FILE}" ]; then
    printf '%s\n' "${LAST}" > "${FIRST_SEEN_FILE}"
    log "INFO: first sync recorded at ${LAST}; future stale checks will run"
fi

AGE_HOURS=$(python3 - "${LAST}" <<'PY'
import sys
from datetime import datetime, timezone
raw = sys.argv[1].strip().replace("T", " ")
for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
    try:
        dt = datetime.strptime(raw, fmt)
        break
    except ValueError:
        continue
else:
    print("-1")
    sys.exit(0)
age = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds() / 3600.0
print(f"{age:.2f}")
PY
)

if [ "${AGE_HOURS}" = "-1" ] || [ -z "${AGE_HOURS}" ]; then
    log "WARN: could not parse last_sync='${LAST}'"
    exit 3
fi

# bash arithmetic expansion only handles integers; compare with awk
IS_STALE=$(awk -v a="${AGE_HOURS}" -v t="${STALE_AFTER_HOURS}" 'BEGIN{print (a>t)?1:0}')

if [ "${IS_STALE}" = "1" ]; then
    log "STALE: last Apple Health sync ${AGE_HOURS}h ago (threshold ${STALE_AFTER_HOURS}h)"
    exit 1
fi

log "OK: last Apple Health sync ${AGE_HOURS}h ago"
exit 0
