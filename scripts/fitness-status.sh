#!/usr/bin/env bash
set -euo pipefail

# Read-only operator status for the local Fitness Dashboard runtime.

APP_LABEL="com.fitness-dashboard"
STALE_LABEL="com.fitness-dashboard.staleness"
APP_PORT="${APP_PORT:-5050}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PLIST="${HOME}/Library/LaunchAgents/${APP_LABEL}.plist"

resolve_data_dir() {
  if [ -n "${DATA_DIR:-}" ]; then
    printf '%s' "$DATA_DIR"
    return
  fi
  if [ -f "$APP_PLIST" ]; then
    plist_data_dir="$(python3 - "$APP_PLIST" 2>/dev/null <<'PY' || true
import plistlib
import sys

with open(sys.argv[1], "rb") as plist_file:
    plist = plistlib.load(plist_file)

print(plist.get("EnvironmentVariables", {}).get("DATA_DIR", ""), end="")
PY
)"
    if [ -n "$plist_data_dir" ]; then
      printf '%s' "$plist_data_dir"
      return
    fi
  fi
  printf '%s' "$REPO_DIR"
}

DATA_DIR="$(resolve_data_dir)"
STALE_LOG="${APPLE_HEALTH_STALENESS_LOG_FILE:-${APPLE_HEALTH_STALENESS_LOG:-/tmp/apple-health-staleness.log}}"

service_state() {
  local label="$1"
  local output
  if ! command -v launchctl >/dev/null 2>&1; then
    printf 'unavailable'
    return
  fi
  output="$(mktemp -t fitness-status.launchctl.XXXXXX)"
  if launchctl print "gui/$(/usr/bin/id -u)/${label}" >"$output" 2>/dev/null; then
    if /usr/bin/grep -F 'state = running' "$output" >/dev/null 2>&1; then
      printf 'running'
    else
      printf 'loaded'
    fi
  else
    printf 'missing'
  fi
  /bin/rm -f "$output"
}

prerequisite_state() {
  if command -v "$1" >/dev/null 2>&1; then
    printf 'ready'
  else
    printf 'missing'
  fi
}

redact_line() {
  /usr/bin/awk '
    {
      lowered = tolower($0)
      quote = "[\"\047]"
      secret_key = "(token|secret|password|cookie|session|credential|api[_-]?key|jwt|sig|signature)"
      if (lowered ~ "(^|[^[:alnum:]_])" quote "?[[:alnum:]_-]*" secret_key "[[:alnum:]_-]*[[:space:]]*" quote "?[[:space:]]*[:=]" ||
          lowered ~ "(^|[^[:alnum:]_])" quote "?(authorization|proxy-authorization|set-cookie)" quote "?[[:space:]]*[:=]" ||
          lowered ~ "(^|[?&{,[:space:]])" quote "?(code|state)" quote "?[[:space:]]*[:=]" ||
          lowered ~ "://[^[:space:]/@]+@") {
        print "[REDACTED]"
        next
      }
      print
    }
  '
}

printf 'app_launchd=%s\n' "$(service_state "$APP_LABEL")"
printf 'staleness_launchd=%s\n' "$(service_state "$STALE_LABEL")"

listener_pid="missing"
if command -v lsof >/dev/null 2>&1; then
  detected_pid="$(lsof -tiTCP:"$APP_PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -1 || true)"
  if [ -n "$detected_pid" ]; then
    listener_pid="$detected_pid"
  fi
fi
printf 'listener_pid=%s\n' "$listener_pid"
printf 'data_dir=%s\n' "$DATA_DIR"

if [ -f "$STALE_LOG" ]; then
  last_staleness_line="$(/usr/bin/tail -n 1 "$STALE_LOG" | redact_line)"
  printf 'staleness_last=%s\n' "${last_staleness_line:-empty}"
else
  printf 'staleness_last=missing\n'
fi

printf 'smoke_curl=%s\n' "$(prerequisite_state curl)"
printf 'smoke_python3=%s\n' "$(prerequisite_state python3)"
printf 'smoke_lsof=%s\n' "$(prerequisite_state lsof)"
if [ -n "${COOKIE:-}" ] || {
  [ -n "${FITNESS_SMOKE_USERNAME:-${SMOKE_USERNAME:-}}" ] &&
  [ -n "${FITNESS_SMOKE_PASSWORD:-${SMOKE_PASSWORD:-}}" ];
}; then
  printf 'smoke_auth=ready\n'
else
  printf 'smoke_auth=missing\n'
fi
