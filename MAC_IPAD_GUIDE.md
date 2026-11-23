# Mac 서버 + 아이패드 접속 및 Windows↔Mac 자동 동기화 가이드

개인 **Mac PC**에서 Windows와 **완전히 동일한 기능**(검색 · 자동 목록 갱신 · 엑셀/PDF 미리보기 · Outlook `.pst` 메일 변환 · Finder 열기)을 실행하고, **아이패드(Safari / 홈 화면 웹앱)**에서 안전하게 접속하는 방법입니다.

---

## 1. Windows에서 기능을 수정하면 Mac에도 자동 반영되는 구조
- Windows에서 기능을 수정하면 항상 GitHub(`main` 브랜치)에 푸시됩니다.
- Mac에서 **`HPNT_Start_Mac.command`를 더블클릭**(또는 `./scripts/start_mac.sh` 실행)하면 **시작 시 자동으로 `git pull --ff-only`**를 수행해 Windows와 100% 동일한 최신 버전으로 실행됩니다.

---

## 2. Mac PC 최초 1회 준비
1. **Google Drive 데스크톱 앱** 설치 및 로그인 (`내 드라이브/HPNT_업무`, `메일보관_백업_2026-10-31` 연결).
2. **Node.js 설치** *(Mac에서도 `.pst` 메일 변환을 쓰려면 필요)*:
   ```bash
   brew install node
   ```
   *(Homebrew가 없다면 https://nodejs.org 에서 macOS LTS 설치)*
3. 터미널에서 저장소 받기:
   ```bash
   git clone https://github.com/yarokim83/Quick-Search.git
   cd Quick-Search
   ```
4. Finder에서 **`HPNT_Start_Mac.command`**를 더블클릭 (또는 터미널에서 `./scripts/start_mac.sh`)하면:
   - Mac 전용 Python 가상환경(`.venv_mac`)에 `openpyxl`, `pypdf`, `python-docx`가 자동 설치되고,
   - Google Drive의 `HPNT_업무` / `메일보관_백업_2026-10-31` 폴더를 자동 탐지하며,
   - 최초 1회 아이패드 접속용 **PIN 번호(4~8자리)**를 입력받아 `~/.hpnt_search_mac.env`에 저장합니다.

> **팁**: Windows PC의 `catalog.sqlite` 및 `mails_cache.sqlite`를 Mac의 `~/Library/Application Support/HPNT_Search/`로 복사해 두면, 처음 본문 색인을 다시 기다리지 않고 즉시 고속 검색을 시작할 수 있습니다. (첫 목록 갱신 시 Mac 경로로 자동 맞춰집니다.)

---

## 3. Mac에서 Windows와 동일하게 쓰는 기능
Mac 브라우저(`http://127.0.0.1:8765`)로 접속하면 Windows와 모든 기능이 동일하게 동작합니다.
- **자동/수동 목록 갱신 (`지금 목록 갱신`)**: Google Drive 변경 파일 감지 및 PDF/엑셀/DOCX 본문 색인
- **Outlook `.pst` 메일 변환·추가**: 드래그 앤 드롭 또는 **`[📁 PC에서 PST 파일 선택]`**(macOS 네이티브 Finder 파일 선택 창) → 자동 변환·중복제거·즉시 검색 반영 → **`[📂 변환된 폴더 열기]`**(Finder로 열기)
- **원본 열기 / 폴더 열기**: macOS 기본 앱(`open`) 및 Finder 위치 표시(`open -R`)

---

## 4. 아이패드에서 접속 & 앱처럼 쓰기
1. 아이패드 **Safari**에서 터미널에 표시된 주소로 접속합니다.
   - 같은 Wi-Fi: `http://192.168.x.x:8765`
   - 외부망 / LTE / 회사 Wi-Fi: Tailscale IP (`http://100.x.x.x:8765`)
2. 설정한 **PIN 번호**를 1회 입력합니다 (30일간 로그인 유지).
3. Safari 상단 **공유 버튼(□↑) → `홈 화면에 추가`**로 iPad 앱처럼 전체 화면 사용.
4. 아이패드에서는 PC 전용 버튼(`탐색기 열기`, `PST 변환`)이 자동 숨김되고 **PDF 인라인 보기 · 엑셀 시트 미리보기 · 원본 다운로드/공유** 중심으로 최적화됩니다.

---

## 5. Mac Mini 24시간 무인 상시 서버 등록 (`launchd` 백그라운드 자동실행)
터미널 창을 열어두지 않고, Mac Mini 재부팅 시 자동 시작 + 매일 새벽 4시 GitHub 최신 버전 자동 반영까지 한 번에 등록하려면:
```bash
cd ~/Quick-Search
./scripts/install_mac_service.sh
```
- **상태 확인**: `./scripts/install_mac_service.sh --status`
- **Windows 수정 직후 Mac 즉시 업데이트**: `./scripts/install_mac_service.sh --restart`
- **자동실행 해제**: `./scripts/install_mac_service.sh --uninstall`

---

## 6. 외부(회사·이동 중)에서도 아이패드로 접속하려면 (Tailscale)
1. **Mac**과 **아이패드(App Store)** 양쪽에 무료 **Tailscale** 앱을 설치하고 같은 계정으로 로그인합니다.
2. Mac에서 `HPNT_Start_Mac.command`를 실행하면 `[아이패드 외부망] http://100.x.x.x:8765` 주소가 자동 출력됩니다.
3. 아이패드에서 Tailscale을 켠 상태로 해당 주소에 접속하면 집 밖에서도 안전하게 사용할 수 있습니다.
