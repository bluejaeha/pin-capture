# CaptureApp — 가벼운 Windows 화면 캡쳐 도구

트레이에 상주하면서 단축키 한 번으로 화면을 캡쳐하는 프로그램입니다.
캡쳐 결과를 화면에 떠 있는 창(플로팅)으로 보여주고, 클립보드 복사·파일 저장을 자동으로 처리합니다.

> **Windows 전용**입니다. Win32 API(DWM, RegisterHotKey, 저수준 훅 등)를 직접 사용하므로 macOS/Linux에서는 동작하지 않습니다.

## 주요 기능

- **영역 캡쳐** — 드래그로 원하는 영역 선택 (`Ctrl+Shift+1`)
- **전체화면 캡쳐** — 모든 모니터를 합친 화면 (`Ctrl+Shift+2`)
- **활성창 캡쳐** — 마지막으로 사용하던 창을 자동 인식 (`Ctrl+Shift+3`)
- **플로팅 창** — 캡쳐 결과를 화면 위에 띄워둠
  - 드래그: 이동 / 휠: 확대·축소 / 더블클릭·Esc: 닫기
  - `Ctrl+C` 복사, `Ctrl+S` 저장, 우클릭: 메뉴
  - 플로팅 창 **밖**을 우클릭하면 닫힘
  - 캡쳐를 다른 앱에 **붙여넣으면(Ctrl+V)** 자동으로 닫힘
- 클립보드 자동 복사 / 파일 자동 저장 / 저장 폴더 지정
- 단축키 사용자 설정 (설정은 `%APPDATA%\CaptureApp\settings.json`에 저장)
- Windows 시작 시 자동 실행 옵션 (트레이로 조용히 시작)
- 모니터별 배율(DPI)이 다른 멀티모니터 환경 지원

## 설치 및 실행 (소스)

```powershell
# 1. 가상환경 생성 + 활성화
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 실행 (콘솔 없이 실행하려면 pythonw)
python main.py --show
```

- 기본은 트레이 아이콘으로만 시작합니다. `--show` 인자를 주면 메인 창이 함께 뜹니다.
- 저장 폴더 기본값: `내 사진\CaptureApp`

## 빌드 (PyInstaller)

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name CaptureApp main.py
```

`dist\CaptureApp.exe` 하나로 배포할 수 있습니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `main.py` | 메인 창·트레이·단축키·캡쳐 흐름 |
| `capture.py` | 화면 캡쳐 (mss + Win32, 멀티모니터 DPI 처리) |
| `region_selector.py` | 드래그 영역 선택 오버레이 |
| `floating_window.py` | 캡쳐 결과 플로팅 창 |
| `hotkeys.py` | 전역 단축키 (RegisterHotKey) |
| `mouse_watcher.py` | 전역 우클릭 감시 (플로팅 창 밖 우클릭 → 닫기) |
| `keyboard_watcher.py` | 전역 Ctrl+V 감시 (붙여넣기 → 플로팅 닫기) |
| `autostart.py` | Windows 시작 시 자동 실행 등록 |
