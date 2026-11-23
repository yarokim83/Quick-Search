#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/Library/Logs"
UID_NUM="$(id -u)"

SERVICE_LABEL="com.hpnt.quicksearch"
SERVICE_PLIST="${LAUNCH_DIR}/${SERVICE_LABEL}.plist"
UPDATER_LABEL="com.hpnt.quicksearch.autoupdate"
UPDATER_PLIST="${LAUNCH_DIR}/${UPDATER_LABEL}.plist"

LOG_OUT="${LOG_DIR}/hpnt_quicksearch.log"
LOG_ERR="${LOG_DIR}/hpnt_quicksearch.err.log"
UPDATER_SCRIPT="${PROJECT_DIR}/scripts/_mac_autoupdate.sh"

find_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then
    command -v tailscale
  elif [[ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
    echo "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
  fi
}

cmd_status() {
  echo "=== HPNT Quick Search macOS 서비스 상태 ==="
  launchctl list | grep -E "com\.hpnt\." || echo "(등록된 서비스 없음)"
  echo ""
  echo "=== 8765 포트 수신 상태 ==="
  lsof -nP -iTCP:8765 -sTCP:LISTEN || echo "(8765 포트 미수신)"
  echo ""
  TS_BIN="$(find_tailscale || true)"
  if [[ -n "${TS_BIN}" ]]; then
    echo "=== Tailscale Serve 상태 ==="
    "${TS_BIN}" serve status 2>/dev/null || true
    echo ""
  fi
  if [[ -f "${LOG_OUT}" ]]; then
    echo "=== 최근 로그 (${LOG_OUT}) ==="
    tail -n 15 "${LOG_OUT}"
  fi
}

cmd_restart() {
  echo "[재시작] 최신 GitHub 버전 반영 및 서버 재시작 중..."
  launchctl kickstart -k "gui/${UID_NUM}/${SERVICE_LABEL}"
  sleep 2
  cmd_status
}

cmd_uninstall() {
  echo "[해제] HPNT Quick Search 자동실행 서비스를 중지·제거합니다..."
  launchctl bootout "gui/${UID_NUM}" "${SERVICE_PLIST}" 2>/dev/null || true
  launchctl bootout "gui/${UID_NUM}" "${UPDATER_PLIST}" 2>/dev/null || true
  rm -f "${SERVICE_PLIST}" "${UPDATER_PLIST}" "${UPDATER_SCRIPT}"
  echo "[완료] 자동실행 서비스가 해제되었습니다."
}

case "${1:-install}" in
  --status|status)
    cmd_status
    exit 0
    ;;
  --restart|restart)
    cmd_restart
    exit 0
    ;;
  --uninstall|uninstall)
    cmd_uninstall
    exit 0
    ;;
esac

mkdir -p "${LAUNCH_DIR}" "${LOG_DIR}"

# 1. Retire legacy LaunchAgents safely (move to disabled_legacy instead of deleting)
LEGACY_BACKUP="${LAUNCH_DIR}/disabled_legacy"
for old_name in com.hpnt.search.local com.hpnt.search.tailscale; do
  old_plist="${LAUNCH_DIR}/${old_name}.plist"
  if [[ -f "${old_plist}" ]]; then
    mkdir -p "${LEGACY_BACKUP}"
    echo "[정리] 구버전 자동실행 중지 및 백업: ${old_name}"
    launchctl bootout "gui/${UID_NUM}" "${old_plist}" 2>/dev/null || true
    mv "${old_plist}" "${LEGACY_BACKUP}/${old_name}.plist"
  fi
done

# 2. Stop any existing process holding port 8765 (e.g. foreground terminal session)
EXISTING_PIDS="$(lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${EXISTING_PIDS}" ]]; then
  echo "[정리] 기존 8765 포트 프로세스 종료: ${EXISTING_PIDS}"
  kill ${EXISTING_PIDS} 2>/dev/null || true
  sleep 1
fi

# 3. Configure Tailscale Serve -> 127.0.0.1:8765 if Tailscale is available
TS_BIN="$(find_tailscale || true)"
if [[ -n "${TS_BIN}" ]]; then
  echo "[Tailscale] HTTPS 프록시를 127.0.0.1:8765로 연결합니다..."
  "${TS_BIN}" serve --bg http://127.0.0.1:8765 >/dev/null 2>&1 || true
fi

# 4. Write main server LaunchAgent plist (RunAtLoad + KeepAlive)
launchctl bootout "gui/${UID_NUM}" "${SERVICE_PLIST}" 2>/dev/null || true
cat > "${SERVICE_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${PROJECT_DIR}/scripts/start_mac.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_ERR}</string>
</dict>
</plist>
EOF

# 5. Write daily 04:00 AM auto-updater helper & LaunchAgent (only restarts if GitHub has a newer commit)
cat > "${UPDATER_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
  exit 0
fi
git -C "${PROJECT_DIR}" fetch --quiet origin main || exit 0
LOCAL_SHA="$(git -C "${PROJECT_DIR}" rev-parse HEAD)"
REMOTE_SHA="$(git -C "${PROJECT_DIR}" rev-parse origin/main)"
if [[ "${LOCAL_SHA}" != "${REMOTE_SHA}" ]]; then
  launchctl kickstart -k "gui/$(id -u)/com.hpnt.quicksearch" || true
fi
EOF
chmod +x "${UPDATER_SCRIPT}"

launchctl bootout "gui/${UID_NUM}" "${UPDATER_PLIST}" 2>/dev/null || true
cat > "${UPDATER_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${UPDATER_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${UPDATER_SCRIPT}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>4</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/hpnt_quicksearch_autoupdate.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/hpnt_quicksearch_autoupdate.log</string>
</dict>
</plist>
EOF

# 6. Load and start both LaunchAgents
launchctl bootstrap "gui/${UID_NUM}" "${SERVICE_PLIST}"
launchctl bootstrap "gui/${UID_NUM}" "${UPDATER_PLIST}"

echo ""
echo "[완료] HPNT Quick Search 백그라운드 상시 서비스가 설치 및 시작되었습니다!"
echo "  - Mac 재부팅 시 자동 시작 + 오류 시 자동 복구 (KeepAlive)"
echo "  - 매일 새벽 04:00 GitHub 새 버전 감지 시 자동 업데이트·재시작"
echo "  - 상태 확인   : ./scripts/install_mac_service.sh --status"
echo "  - 즉시 업데이트: ./scripts/install_mac_service.sh --restart"
echo "  - 서비스 해제 : ./scripts/install_mac_service.sh --uninstall"
echo ""
sleep 2
cmd_status
