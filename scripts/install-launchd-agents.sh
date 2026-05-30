#!/bin/bash
# Install or remove the local Fitness Dashboard launchd agents.

set -euo pipefail

ACTION="install"
DRY_RUN=0
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN=""
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
APP_LABEL="com.fitness-dashboard"
STALE_LABEL="com.fitness-dashboard.staleness"
APP_PLIST="${LAUNCH_AGENTS_DIR}/${APP_LABEL}.plist"
STALE_PLIST="${LAUNCH_AGENTS_DIR}/${STALE_LABEL}.plist"
APP_LOG="/tmp/fitness-dashboard.log"
APP_ERR="/tmp/fitness-dashboard.err.log"
STALE_LOG="/tmp/fitness-dashboard-staleness.log"
STALE_ERR="/tmp/fitness-dashboard-staleness.err.log"
APP_HOST="127.0.0.1"
APP_PORT="5050"
APP_FLASK_DEBUG="0"

usage() {
    cat <<'USAGE'
Usage: scripts/install-launchd-agents.sh [install|uninstall|reinstall] [--dry-run] [--repo-dir PATH] [--python PATH]

Installs two user launchd agents:
  com.fitness-dashboard
  com.fitness-dashboard.staleness

Options:
  --dry-run       Print the actions and generated plists without writing files or calling launchctl.
  --repo-dir      Fitness Dashboard repo path. Defaults to the parent of this script.
  --python        Python executable for app.py. Defaults to .venv/bin/python, venv/bin/python, then python3.
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        install|uninstall|reinstall)
            ACTION="$1"
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --repo-dir)
            shift
            REPO_DIR="${1:?missing --repo-dir value}"
            ;;
        --python)
            shift
            PYTHON_BIN="${1:?missing --python value}"
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

REPO_DIR="$(cd "${REPO_DIR}" && pwd)"
DATA_DIR="${DATA_DIR:-${REPO_DIR}}"
PUBLIC_BASE_URL="${FITNESS_DASHBOARD_PUBLIC_BASE_URL:-}"
if [ -z "${PYTHON_BIN}" ]; then
    if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
        PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
    elif [ -x "${REPO_DIR}/venv/bin/python" ]; then
        PYTHON_BIN="${REPO_DIR}/venv/bin/python"
    else
        PYTHON_BIN="$(command -v python3)"
    fi
fi

run() {
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

bootout_label() {
    local label="$1"
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] launchctl bootout gui/%s/%s || true\n' "$(id -u)" "${label}"
        return
    fi
    launchctl bootout "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
}

bootstrap_plist() {
    local plist="$1"
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] launchctl bootstrap gui/%s %s\n' "$(id -u)" "${plist}"
        return
    fi
    launchctl bootstrap "gui/$(id -u)" "${plist}"
}

kickstart_label() {
    local label="$1"
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] launchctl kickstart -k gui/%s/%s\n' "$(id -u)" "${label}"
        return
    fi
    launchctl kickstart -k "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
}

write_if_changed() {
    local target="$1"
    local content="$2"
    if [ "${DRY_RUN}" = "1" ]; then
        printf '[dry-run] write %s\n%s\n' "${target}" "${content}"
        return
    fi
    local tmp
    tmp="$(mktemp "${target}.XXXXXX")"
    printf '%s\n' "${content}" > "${tmp}"
    if [ -f "${target}" ] && cmp -s "${tmp}" "${target}"; then
        rm -f "${tmp}"
        printf 'unchanged %s\n' "${target}"
    else
        mv "${tmp}" "${target}"
        printf 'wrote %s\n' "${target}"
    fi
}

xml_escape() {
    local value="$1"
    value="${value//&/&amp;}"
    value="${value//</&lt;}"
    value="${value//>/&gt;}"
    printf '%s' "${value}"
}

app_plist() {
    local app_label repo_dir python_bin app_path app_log app_err app_host app_port app_debug data_dir public_base_url
    app_label="$(xml_escape "${APP_LABEL}")"
    repo_dir="$(xml_escape "${REPO_DIR}")"
    python_bin="$(xml_escape "${PYTHON_BIN}")"
    app_path="$(xml_escape "${REPO_DIR}/app.py")"
    app_log="$(xml_escape "${APP_LOG}")"
    app_err="$(xml_escape "${APP_ERR}")"
    app_host="$(xml_escape "${APP_HOST}")"
    app_port="$(xml_escape "${APP_PORT}")"
    app_debug="$(xml_escape "${APP_FLASK_DEBUG}")"
    data_dir="$(xml_escape "${DATA_DIR}")"
    public_base_url="$(xml_escape "${PUBLIC_BASE_URL}")"
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${app_label}</string>
  <key>WorkingDirectory</key>
  <string>${repo_dir}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${python_bin}</string>
    <string>${app_path}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key>
    <string>${app_host}</string>
    <key>PORT</key>
    <string>${app_port}</string>
    <key>FLASK_DEBUG</key>
    <string>${app_debug}</string>
    <key>DATA_DIR</key>
    <string>${data_dir}</string>
    <key>FITNESS_DASHBOARD_PUBLIC_BASE_URL</key>
    <string>${public_base_url}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${app_log}</string>
  <key>StandardErrorPath</key>
  <string>${app_err}</string>
</dict>
</plist>
PLIST
}

staleness_plist() {
    local stale_label repo_dir script_path sync_db first_seen stale_log stale_err data_dir
    stale_label="$(xml_escape "${STALE_LABEL}")"
    repo_dir="$(xml_escape "${REPO_DIR}")"
    script_path="$(xml_escape "${REPO_DIR}/scripts/check-apple-health-staleness.sh")"
    sync_db="$(xml_escape "${DATA_DIR}/apple_health_sync.db")"
    first_seen="$(xml_escape "${DATA_DIR}/.apple-health-first-sync")"
    stale_log="$(xml_escape "${STALE_LOG}")"
    stale_err="$(xml_escape "${STALE_ERR}")"
    data_dir="$(xml_escape "${DATA_DIR}")"
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${stale_label}</string>
  <key>WorkingDirectory</key>
  <string>${repo_dir}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${script_path}</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DATA_DIR</key>
    <string>${data_dir}</string>
    <key>APPLE_HEALTH_SYNC_DB</key>
    <string>${sync_db}</string>
    <key>APPLE_HEALTH_FIRST_SEEN_FILE</key>
    <string>${first_seen}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${stale_log}</string>
  <key>StandardErrorPath</key>
  <string>${stale_err}</string>
</dict>
</plist>
PLIST
}

install_agents() {
    run mkdir -p "${LAUNCH_AGENTS_DIR}"
    write_if_changed "${APP_PLIST}" "$(app_plist)"
    write_if_changed "${STALE_PLIST}" "$(staleness_plist)"
    bootout_label "${APP_LABEL}"
    bootout_label "${STALE_LABEL}"
    bootstrap_plist "${APP_PLIST}"
    bootstrap_plist "${STALE_PLIST}"
    kickstart_label "${APP_LABEL}"
    kickstart_label "${STALE_LABEL}"
}

uninstall_agents() {
    bootout_label "${APP_LABEL}"
    bootout_label "${STALE_LABEL}"
    run rm -f "${APP_PLIST}" "${STALE_PLIST}"
}

case "${ACTION}" in
    install)
        install_agents
        ;;
    uninstall)
        uninstall_agents
        ;;
    reinstall)
        uninstall_agents
        install_agents
        ;;
esac
