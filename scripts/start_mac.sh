#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${HOME}/.hpnt_search_mac.env"

if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

# 1. Auto-detect Google Drive HPNT_업무 folder on macOS
if [[ -z "${HPNT_ROOT:-}" || ! -d "${HPNT_ROOT}" ]]; then
  FOUND_ROOT=""
  for base in "${HOME}/Library/CloudStorage"/GoogleDrive-*; do
    for sub in "My Drive/HPNT_업무" "내 드라이브/HPNT_업무"; do
      if [[ -d "${base}/${sub}" ]]; then
        FOUND_ROOT="${base}/${sub}"
        break 2
      fi
    done
  done
  if [[ -n "${FOUND_ROOT}" ]]; then
    HPNT_ROOT="${FOUND_ROOT}"
  else
    echo "[오류] Google Drive의 'HPNT_업무' 폴더를 자동으로 찾지 못했습니다."
    read -r -p "HPNT_업무 폴더 전체 경로를 입력해 주세요: " HPNT_ROOT
    HPNT_ROOT="${HPNT_ROOT/#\~/$HOME}"
    if [[ ! -d "${HPNT_ROOT}" ]]; then
      echo "[오류] 입력한 폴더가 존재하지 않습니다: ${HPNT_ROOT}"
      exit 1
    fi
  fi
fi

# 2. Auto-detect sibling mail archive folder if present
if [[ -z "${HPNT_MAIL_ROOT:-}" ]]; then
  DRIVE_ROOT="$(dirname "${HPNT_ROOT}")"
  if [[ -d "${DRIVE_ROOT}/메일보관_백업_2026-10-31" ]]; then
    HPNT_MAIL_ROOT="${DRIVE_ROOT}/메일보관_백업_2026-10-31"
  fi
fi

# 3. Local SQLite DB location on macOS
DB_DIR="${HOME}/Library/Application Support/HPNT_Search"
mkdir -p "${DB_DIR}"
HPNT_DB="${HPNT_DB:-${DB_DIR}/catalog.sqlite}"
HPNT_PORT="${HPNT_PORT:-8765}"
HPNT_HOST="${HPNT_HOST:-0.0.0.0}"

# 4. Access PIN for iPad / remote devices (saved once in ~/.hpnt_search_mac.env)
if [[ -z "${HPNT_ACCESS_PIN:-}" ]]; then
  echo ""
  echo "아이패드 등 원격 기기 접속 시 사용할 PIN 번호(숫자 4~8자리 권장)를 설정해 주세요."
  echo "(비워두고 Enter를 누르면 PIN 없이 바로 접속합니다)"
  read -r -s -p "접속 PIN: " HPNT_ACCESS_PIN
  echo ""
  {
    printf 'HPNT_ROOT=%q\n' "${HPNT_ROOT}"
    [[ -n "${HPNT_MAIL_ROOT:-}" ]] && printf 'HPNT_MAIL_ROOT=%q\n' "${HPNT_MAIL_ROOT}"
    printf 'HPNT_DB=%q\n' "${HPNT_DB}"
    printf 'HPNT_PORT=%q\n' "${HPNT_PORT}"
    printf 'HPNT_HOST=%q\n' "${HPNT_HOST}"
    printf 'HPNT_ACCESS_PIN=%q\n' "${HPNT_ACCESS_PIN}"
  } > "${CONFIG_FILE}"
  chmod 600 "${CONFIG_FILE}"
  echo "설정이 ${CONFIG_FILE} 에 저장되었습니다."
fi

export HPNT_ROOT HPNT_DB HPNT_HOST HPNT_ACCESS_PIN
if [[ -n "${HPNT_MAIL_ROOT:-}" ]]; then
  export HPNT_MAIL_ROOT
fi

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[오류] python3를 찾을 수 없습니다. (xcode-select --install 또는 Homebrew로 python3 설치 필요)"
  exit 1
fi

WIFI_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
TS_IP=""
if command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
fi

echo ""
echo "============================================================"
echo "  HPNT 업무자료 통합 검색 서버 (macOS / iPad 호스팅)"
echo "============================================================"
echo "  업무 폴더 : ${HPNT_ROOT}"
echo "  메일 보관 : ${HPNT_MAIL_ROOT:-(미탐지)}"
echo "  로컬 DB   : ${HPNT_DB}"
echo "  PIN 잠금  : $([[ -n "${HPNT_ACCESS_PIN}" ]] && echo '사용 중' || echo '미사용')"
echo "------------------------------------------------------------"
echo "  [Mac 브라우저]     http://127.0.0.1:${HPNT_PORT}"
if [[ -n "${WIFI_IP}" ]]; then
  echo "  [아이패드 Wi-Fi]   http://${WIFI_IP}:${HPNT_PORT}"
fi
if [[ -n "${TS_IP}" ]]; then
  echo "  [아이패드 외부망]  http://${TS_IP}:${HPNT_PORT}  (Tailscale)"
fi
echo "============================================================"
echo "  종료하려면 Ctrl+C 를 누르세요."
echo ""

exec "${PYTHON_BIN}" -X utf8 -B "${PROJECT_DIR}/app/server.py" \
  --host "${HPNT_HOST}" \
  --port "${HPNT_PORT}" \
  --pin "${HPNT_ACCESS_PIN}"
