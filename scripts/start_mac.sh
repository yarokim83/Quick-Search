#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${HOME}/.hpnt_search_mac.env"
NO_UPDATE=0
for arg in "$@"; do
  [[ "${arg}" == "--no-update" ]] && NO_UPDATE=1
done

# Ensure Homebrew / local binaries are on PATH (important when double-clicked via .command)
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

# 0. Auto-sync latest updates from GitHub (Windows 수정 → GitHub 푸시 → Mac 자동 반영)
if [[ "${NO_UPDATE}" -eq 0 && -d "${PROJECT_DIR}/.git" ]] && command -v git >/dev/null 2>&1; then
  if git -C "${PROJECT_DIR}" diff --quiet 2>/dev/null && git -C "${PROJECT_DIR}" diff --cached --quiet 2>/dev/null; then
    echo "[동기화] GitHub 최신 버전 확인 중..."
    if git -C "${PROJECT_DIR}" pull --ff-only origin main >/dev/null 2>&1; then
      CUR_VER="$(cat "${PROJECT_DIR}/VERSION" 2>/dev/null || echo 'unknown')"
      echo "[동기화] 최신 버전 적용 완료 (v${CUR_VER})"
    else
      echo "[동기화] 오프라인 또는 원격 확인 불가 — 현재 로컬 버전으로 실행합니다."
    fi
  else
    echo "[동기화] 로컬 수정 사항이 있어 자동 업데이트를 건너뜁니다."
  fi
fi

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
if [[ -z "${HPNT_ACCESS_PIN:-}" && ! -f "${CONFIG_FILE}" ]]; then
  if [[ -t 0 ]]; then
    echo ""
    echo "아이패드 등 원격 기기 접속 시 사용할 PIN 번호(숫자 4~8자리 권장)를 설정해 주세요."
    echo "(비워두고 Enter를 누르면 PIN 없이 바로 접속합니다)"
    read -r -s -p "접속 PIN: " HPNT_ACCESS_PIN
    echo ""
  else
    HPNT_ACCESS_PIN=""
  fi
  cat > "${CONFIG_FILE}" <<EOF
HPNT_ROOT='${HPNT_ROOT}'
HPNT_MAIL_ROOT='${HPNT_MAIL_ROOT:-}'
HPNT_DB='${HPNT_DB}'
HPNT_PORT='${HPNT_PORT}'
HPNT_HOST='${HPNT_HOST}'
HPNT_ACCESS_PIN='${HPNT_ACCESS_PIN}'
EOF
  chmod 600 "${CONFIG_FILE}"
  echo "설정이 ${CONFIG_FILE} 에 저장되었습니다."
fi

export HPNT_ROOT HPNT_DB HPNT_HOST HPNT_ACCESS_PIN
if [[ -n "${HPNT_MAIL_ROOT:-}" ]]; then
  export HPNT_MAIL_ROOT
fi

# 5. Prepare Python runtime (.venv_mac with openpyxl/pypdf/python-docx for full feature parity)
SYS_PYTHON="$(command -v python3 || true)"
if [[ -z "${SYS_PYTHON}" ]]; then
  echo "[오류] python3를 찾을 수 없습니다. (터미널에서 xcode-select --install 또는 brew install python 실행)"
  exit 1
fi

VENV_DIR="${PROJECT_DIR}/.venv_mac"
PYTHON_BIN="${VENV_DIR}/bin/python3"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[환경준비] Mac 전용 Python 가상환경(.venv_mac)을 생성합니다..."
  "${SYS_PYTHON}" -m venv "${VENV_DIR}"
fi
if ! "${PYTHON_BIN}" -c "import openpyxl, pypdf, docx" >/dev/null 2>&1; then
  echo "[환경준비] 엑셀/PDF/DOCX 본문 색인 및 미리보기 패키지 설치 중..."
  "${PYTHON_BIN}" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "${PYTHON_BIN}" -m pip install --quiet openpyxl pypdf python-docx || echo "[경고] 일부 패키지 설치 실패 — 기본 검색은 정상 동작합니다."
fi

# 6. Detect Node.js (needed for Outlook .pst conversion)
NODE_BIN="$(command -v node || true)"
if [[ -z "${NODE_BIN}" && -d "${HOME}/.nvm/versions/node" ]]; then
  NODE_BIN="$(ls -1d "${HOME}/.nvm/versions/node"/*/bin/node 2>/dev/null | tail -n 1 || true)"
fi

WIFI_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
TS_BIN="$(command -v tailscale || true)"
if [[ -z "${TS_BIN}" && -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]]; then
  TS_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi
TS_IP=""
TS_HTTPS=""
if [[ -n "${TS_BIN}" ]]; then
  TS_IP="$("${TS_BIN}" ip -4 2>/dev/null | head -n 1 || true)"
  TS_HTTPS="$("${TS_BIN}" serve status 2>/dev/null | grep -Eo 'https://[^ ]+\.ts\.net' | head -n 1 || true)"
fi
APP_VER="$(cat "${PROJECT_DIR}/VERSION" 2>/dev/null || echo 'unknown')"

echo ""
echo "============================================================"
echo "  HPNT 업무자료 통합 검색 서버 v${APP_VER} (macOS / iPad)"
echo "============================================================"
echo "  업무 폴더 : ${HPNT_ROOT}"
echo "  메일 보관 : ${HPNT_MAIL_ROOT:-(미탐지)}"
echo "  로컬 DB   : ${HPNT_DB}"
echo "  PIN 잠금  : $([[ -n "${HPNT_ACCESS_PIN}" ]] && echo '사용 중' || echo '미사용')"
if [[ -n "${NODE_BIN}" ]]; then
  echo "  PST 변환  : 사용 가능 (Node: ${NODE_BIN})"
else
  echo "  PST 변환  : Node.js 미설치 (brew install node 후 사용 가능)"
fi
echo "------------------------------------------------------------"
echo "  [Mac 브라우저]     http://127.0.0.1:${HPNT_PORT}"
if [[ -n "${WIFI_IP}" ]]; then
  echo "  [아이패드 Wi-Fi]   http://${WIFI_IP}:${HPNT_PORT}"
fi
if [[ -n "${TS_HTTPS}" ]]; then
  echo "  [아이패드 HTTPS]   ${TS_HTTPS}  (Tailscale Serve)"
elif [[ -n "${TS_IP}" ]]; then
  echo "  [아이패드 외부망]  http://${TS_IP}:${HPNT_PORT}  (Tailscale)"
fi
echo "============================================================"
echo "  종료하려면 Ctrl+C 를 누르세요."
echo ""

exec "${PYTHON_BIN}" -X utf8 -B "${PROJECT_DIR}/app/server.py" \
  --host "${HPNT_HOST}" \
  --port "${HPNT_PORT}" \
  --pin "${HPNT_ACCESS_PIN}"
