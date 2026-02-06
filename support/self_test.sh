#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:5050}"

echo "Self-test against: $BASE_URL"

hit() {
  local path="$1"
  echo "\n==> GET $path"
  local body
  body="$(curl -sS "$BASE_URL$path")"

  # Try JSON pretty-print, fall back to raw.
  if printf "%s" "$body" | python -m json.tool >/dev/null 2>&1; then
    printf "%s" "$body" | python -m json.tool | sed -n '1,80p'
  else
    printf "%s\n" "$body" | sed -n '1,80p'
  fi
}

hit "/api/dashboard"
hit "/api/settings"
hit "/api/history"
hit "/api/history-all"
hit "/api/oura/status"
hit "/api/oura/trends"
hit "/api/weather"
hit "/api/recommendation/smart"

echo "\nOK"
