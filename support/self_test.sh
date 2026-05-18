#!/usr/bin/env bash
set -euo pipefail

# Live authenticated smoke test.
#
# This script intentionally exercises a running Flask server and real session
# cookie. Offline unit and route-shape coverage lives under tests/ and runs with
# pytest.

BASE_URL="${BASE_URL:-http://127.0.0.1:5050}"
COOKIE="${COOKIE:-}"
SMOKE_USERNAME="${FITNESS_SMOKE_USERNAME:-${SMOKE_USERNAME:-}}"
SMOKE_PASSWORD="${FITNESS_SMOKE_PASSWORD:-${SMOKE_PASSWORD:-}}"
COOKIE_JAR="${COOKIE_JAR:-/tmp/fitness-dashboard-self-test.cookies}"
BODY_FILE="${BODY_FILE:-/tmp/fitness-dashboard-self-test.json}"
export BODY_FILE

rm -f "$COOKIE_JAR" "$BODY_FILE"

echo "Self-test against: $BASE_URL"

curl_auth_args=()
if [ -n "$COOKIE" ]; then
  curl_auth_args=(-H "Cookie: session=${COOKIE}")
elif [ -n "$SMOKE_USERNAME" ] && [ -n "$SMOKE_PASSWORD" ]; then
  echo "==> POST /login"
  login_code="$(
    curl -sS -L -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
      -o "$BODY_FILE" \
      -w "%{http_code}" \
      --data-urlencode "username=${SMOKE_USERNAME}" \
      --data-urlencode "password=${SMOKE_PASSWORD}" \
      "$BASE_URL/login"
  )"
  if [ "$login_code" -lt 200 ] || [ "$login_code" -ge 300 ]; then
    echo "FAIL: login returned HTTP $login_code" >&2
    exit 1
  fi
  if grep -i "Invalid username or password" "$BODY_FILE" >/dev/null 2>&1; then
    echo "FAIL: login rejected supplied smoke credentials" >&2
    exit 1
  fi
  curl_auth_args=(-b "$COOKIE_JAR")
else
  echo "FAIL: provide COOKIE=session-value or FITNESS_SMOKE_USERNAME/FITNESS_SMOKE_PASSWORD" >&2
  exit 1
fi

assert_no_sensitive_material() {
  local path="$1"
  if grep -E '"raw_json"|[?&]token=|HEALTH_SYNC_TOKEN|APPLE_HEALTH_WEBHOOK_URL' "$BODY_FILE" >/dev/null 2>&1; then
    echo "FAIL: $path response includes sensitive raw/token material" >&2
    return 1
  fi
}

hit() {
  local path="$1"
  echo "\n==> GET $path"
  local body
  local code
  local curl_args=(-sS -o "$BODY_FILE" -w "%{http_code}")
  curl_args+=("${curl_auth_args[@]}")
  code="$(curl "${curl_args[@]}" "$BASE_URL$path")"
  body="$(cat "$BODY_FILE")"

  assert_no_sensitive_material "$path"
  python3 - "$path" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    import os
    with open(os.environ.get("BODY_FILE", "/tmp/fitness-dashboard-self-test.json"), "r", encoding="utf-8") as fh:
        body = json.load(fh)
except Exception:
    print("body=non-json")
    raise SystemExit

summary = {"path": path}
if path == "/api/dashboard":
    summary["dashboard_keys"] = sorted(body.keys())[:8]
elif path == "/api/settings":
    summary["equipment_preference"] = body.get("equipment_preference")
    summary["equipment_options"] = [o.get("label") for o in body.get("equipment_options", [])]
elif path == "/api/history":
    summary["count"] = body.get("count")
elif path == "/api/history-all":
    summary["workouts"] = len(body.get("workouts", []))
    summary["cardio"] = len(body.get("cardio", []))
elif path == "/api/oura/status":
    summary["source"] = body.get("source")
    summary["date"] = body.get("date")
    summary["readiness"] = body.get("readiness")
elif path == "/api/oura/trends":
    summary["series_days"] = len(body.get("series", []))
    summary["hrv_trend"] = body.get("hrv_trend")
elif path == "/api/apple-health/sync/status":
    summary["last_sync"] = body.get("last_sync")
    summary["last_record_date"] = body.get("last_record_date")
elif path == "/api/weather":
    summary["source"] = body.get("source")
    summary["condition"] = body.get("condition")
elif path == "/api/recommendation/smart":
    summary["recommendation"] = body.get("recommendation")
    summary["effective_readiness"] = body.get("effective_readiness")
elif path == "/api/ai/health":
    summary["active_role"] = body.get("active_role")
    summary["model_loaded"] = body.get("model_loaded")
    summary["routes"] = [
        {"role": c.get("role"), "reachable": c.get("reachable"), "model_loaded": c.get("model_loaded")}
        for c in body.get("checks", [])
    ]
else:
    summary["keys"] = sorted(body.keys())[:8] if isinstance(body, dict) else type(body).__name__
print(json.dumps(summary, sort_keys=True))
PY
  if [ "$code" -lt 200 ] || [ "$code" -ge 300 ]; then
    echo "FAIL: $path returned HTTP $code" >&2
    return 1
  fi
}

post_expect_error() {
  local path="$1"
  local payload="$2"
  local expected="$3"
  echo "\n==> POST $path expected=$expected"
  local code
  local curl_args=(-sS -o "$BODY_FILE" -w "%{http_code}" -H "Content-Type: application/json" -d "$payload")
  curl_args+=("${curl_auth_args[@]}")
  code="$(curl "${curl_args[@]}" "$BASE_URL$path")"
  assert_no_sensitive_material "$path"
  python3 - "$path" <<'PY'
import json
import os
import sys

path = sys.argv[1]
with open(os.environ.get("BODY_FILE", "/tmp/fitness-dashboard-self-test.json"), "r", encoding="utf-8") as fh:
    body = json.load(fh)
print(json.dumps({"path": path, "status": body.get("status"), "error": body.get("error")}, sort_keys=True))
PY
  if [ "$code" != "$expected" ]; then
    echo "FAIL: $path returned HTTP $code, expected $expected" >&2
    return 1
  fi
}

hit "/api/dashboard"
hit "/api/settings"
hit "/api/history"
hit "/api/history-all"
hit "/api/oura/status"
hit "/api/oura/trends"
hit "/api/apple-health/sync/status"
hit "/api/weather"
hit "/api/recommendation/smart"
hit "/api/ai/health"

python3 - <<'PY'
import json
import os
import sys

with open(os.environ.get("BODY_FILE", "/tmp/fitness-dashboard-self-test.json"), "r", encoding="utf-8") as fh:
    health = json.load(fh)

if not health.get("reachable") or not health.get("model_loaded"):
    print("FAIL: /api/ai/health is not reachable with a loaded model", file=sys.stderr)
    sys.exit(1)

candidates = health.get("candidates") or health.get("checks") or []
primary = next((c for c in candidates if c.get("role") == "primary"), None)
fallback = next((c for c in candidates if c.get("role") == "fallback"), None)
if not primary or not fallback:
    print("FAIL: /api/ai/health did not report both primary and fallback routes", file=sys.stderr)
    sys.exit(1)
if not fallback.get("reachable") or not fallback.get("model_loaded"):
    print("FAIL: Mac fallback LM Studio route is degraded", file=sys.stderr)
    sys.exit(1)
primary_healthy = bool(primary.get("reachable") and primary.get("model_loaded"))
if primary_healthy:
    if health.get("active_role") != "primary":
        print(f"FAIL: ASUS primary is healthy but active_role is {health.get('active_role')!r}", file=sys.stderr)
        sys.exit(1)
    if health.get("fallback_active"):
        print("FAIL: Mac fallback is active while ASUS primary is healthy", file=sys.stderr)
        sys.exit(1)
else:
    if health.get("active_role") != "fallback" or not health.get("fallback_active"):
        print(f"FAIL: ASUS primary is degraded but Mac fallback is not active (active_role={health.get('active_role')!r})", file=sys.stderr)
        sys.exit(1)
    print("WARN: ASUS primary route is degraded; Mac fallback is active")
PY

post_expect_error "/api/complete-workout" '{"client_workout_id":"smoke-invalid-empty","offline":true,"exercises":[]}' "400"

if ! command -v lsof >/dev/null 2>&1; then
  echo "FAIL: lsof is required for FD leak regression check" >&2
  exit 1
fi
if [[ ! "$BASE_URL" =~ :([0-9]+)$ ]]; then
  echo "FAIL: cannot infer listener port from BASE_URL=$BASE_URL for FD check" >&2
  exit 1
fi

port="${BASH_REMATCH[1]}"
pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN | head -1 || true)"
if [ -z "$pid" ]; then
  echo "FAIL: no listener PID found on port $port for FD check" >&2
  exit 1
fi

fd_counts() {
  lsof -p "$pid" | awk 'NR>1 {total++} NR>1 && $9 ~ /auth.db/ {auth++} END {print total+0, auth+0}'
}

read -r fd_before auth_fd_before < <(fd_counts)
for _ in $(seq 1 25); do
  curl_args=(-sS -o /dev/null -w "%{http_code}")
  curl_args+=("${curl_auth_args[@]}")
  code="$(curl "${curl_args[@]}" "$BASE_URL/api/settings")"
  if [ "$code" -lt 200 ] || [ "$code" -ge 300 ]; then
    echo "FAIL: FD regression loop /api/settings returned HTTP $code" >&2
    exit 1
  fi
done
read -r fd_after auth_fd_after < <(fd_counts)
fd_delta=$((fd_after - fd_before))
auth_fd_delta=$((auth_fd_after - auth_fd_before))

echo "\n==> FD check pid=$pid before=$fd_before after=$fd_after delta=$fd_delta auth.db.before=$auth_fd_before auth.db.after=$auth_fd_after auth.db.delta=$auth_fd_delta"
if [ "$fd_after" -ge 200 ]; then
  echo "FAIL: process is near launchd maxfiles limit ($fd_after open FDs)" >&2
  exit 1
fi
if [ "$fd_delta" -gt 8 ]; then
  echo "FAIL: FD leak suspected after repeated authenticated requests (delta=$fd_delta)" >&2
  exit 1
fi
if [ "$auth_fd_after" -gt 8 ] || [ "$auth_fd_delta" -gt 1 ]; then
  echo "FAIL: auth.db FD leak suspected (after=$auth_fd_after delta=$auth_fd_delta)" >&2
  exit 1
fi

echo "\nOK"
