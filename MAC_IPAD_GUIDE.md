# Mac 서버 + 아이패드 접속 가이드 (HPNT Quick Search)

개인 **Mac PC**를 검색 서버로 두고, **아이패드(Safari / 홈 화면 웹앱)**에서 업무자료와 보관 메일을 검색·미리보기·다운로드하는 방법입니다.

---

## 1. Mac PC 최초 1회 준비
1. **Google Drive 데스크톱 앱**을 설치하고 로그인하여 `내 드라이브/HPNT_업무` (및 `메일보관_백업_2026-10-31`)가 Mac에 보이도록 연결합니다.
2. 터미널을 열고 이 저장소를 받습니다(이미 받았다면 `git pull`):
   ```bash
   git clone https://github.com/yarokim83/Quick-Search.git
   cd Quick-Search
   chmod +x scripts/start_mac.sh
   ```
3. (선택 · PDF/엑셀 미리보기 패키지) 엑셀 미리보기 등을 위해 Python 패키지가 없다면 한 번만 설치합니다:
   ```bash
   python3 -m pip install --user openpyxl xlrd pypdf
   ```

---

## 2. Mac에서 서버 실행
```bash
./scripts/start_mac.sh
```
- 처음 실행 시 Google Drive의 `HPNT_업무` 폴더를 자동 탐지하고, 아이패드 접속용 **PIN 번호(4~8자리)**를 1회 입력받아 `~/.hpnt_search_mac.env`에 저장합니다.
- 실행되면 터미널에 아래와 같이 접속 주소가 표시됩니다.
  - **같은 Wi-Fi**: `http://192.168.x.x:8765`
  - **외부망 / LTE / 회사 Wi-Fi**: Tailscale IP (`http://100.x.x.x:8765`)

> **팁**: 기존 Windows PC의 `catalog.sqlite` 및 `mails_cache.sqlite`를 Mac의 `~/Library/Application Support/HPNT_Search/`로 복사해 두면, 처음 본문 색인을 다시 기다리지 않고 즉시 고속 검색을 시작할 수 있습니다. (경로는 첫 자동 스캔 시 Mac 경로로 자동 갱신됩니다.)

---

## 3. 아이패드에서 접속 & 앱처럼 쓰기
1. 아이패드 **Safari**에서 터미널에 표시된 주소(`http://<Mac IP>:8765`)로 접속합니다.
2. 설정한 **PIN 번호**를 1회 입력합니다 (로그인 상태가 30일간 유지됩니다).
3. Safari 상단 **공유 버튼(□↑) → `홈 화면에 추가`**를 누르면 일반 iPad 앱처럼 전체 화면으로 사용할 수 있습니다.
4. 아이패드에서 접속하면 PC 전용 버튼(`PC 탐색기 열기`, `PST 변환`)은 자동으로 숨겨지고, **PDF 인라인 보기 · 엑셀 시트 미리보기 · 원본 다운로드/공유** 중심으로 최적화됩니다.

---

## 4. 외부(회사·이동 중)에서도 아이패드로 접속하려면 (Tailscale 추천)
회사 Windows PC와 무관하게 **개인 Mac + 개인 아이패드** 간에만 안전한 개인 VPN을 연결하는 가장 간단한 방법입니다.
1. **Mac**과 **아이패드(App Store)** 양쪽에 무료 앱 **Tailscale**을 설치하고 같은 계정으로 로그인합니다.
2. Mac에서 `./scripts/start_mac.sh`를 실행하면 `[아이패드 외부망] http://100.x.x.x:8765` 주소가 자동 표시됩니다.
3. 아이패드에서 Tailscale을 켠 상태로 해당 주소에 접속하면 집 밖에서도 안전하게 검색할 수 있습니다.
