"""메인 컨트롤 창 — 캡쳐 버튼들, 트레이 아이콘, 전역 단축키."""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import autostart
from capture import (
    active_window_rect,
    grab_fullscreen,
    grab_logical_region,
    grab_region,
    window_rect,
)
from floating_window import FloatingImage, pil_to_clipboard_image, save_pil_with_dpi
from hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    GlobalHotkeys,
)
from keyboard_watcher import GlobalPasteWatcher
from mouse_watcher import GlobalRightClickWatcher
from region_selector import RegionSelector


# 핫키 ID (의미 있는 상수로)
HK_REGION = 1
HK_FULL = 2
HK_ACTIVE = 3
HK_KEYS = ["region", "full", "active"]  # 설정 키 이름
HK_LABEL = {"region": "영역 캡쳐", "full": "전체화면", "active": "활성창"}
HK_ID = {"region": HK_REGION, "full": HK_FULL, "active": HK_ACTIVE}

DEFAULT_HOTKEYS = {
    "region": "Ctrl+Shift+1",
    "full": "Ctrl+Shift+2",
    "active": "Ctrl+Shift+3",
}


# ---- 활성창 추적용 Win32 설정 ----
# 64비트에서 핸들이 잘리지 않도록 시그니처를 명시한다.
_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

# 활성창 추적에서 제외할 셸(작업표시줄/바탕화면) 창 클래스
_SHELL_WINDOW_CLASSES = {
    "Shell_TrayWnd",            # 주 작업표시줄
    "Shell_SecondaryTrayWnd",   # 보조 모니터 작업표시줄
    "NotifyIconOverflowWindow",  # 트레이 넘침 팝업
    "Progman",                  # 바탕화면
    "WorkerW",                  # 바탕화면 워커
}


# ---- 설정 파일 (사용자 단축키 등) ----
SETTINGS_DIR = Path(os.environ.get("APPDATA") or str(Path.home())) / "CaptureApp"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[settings] save error: {exc}", file=sys.stderr)


# ---- Qt 키 시퀀스 → Windows RegisterHotKey (mods, vk) ----
def _vk_for_key(key: str) -> int | None:
    key = key.upper()
    if len(key) == 1 and (
        ("A" <= key <= "Z") or ("0" <= key <= "9")
    ):
        return ord(key)
    if key.startswith("F") and len(key) > 1 and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return 0x6F + n  # VK_F1 == 0x70
    special = {
        "PRINT": 0x2A,
        "SYSREQ": 0x2C,
        "PRINTSCREEN": 0x2C,
        "PRTSC": 0x2C,
        "INSERT": 0x2D, "INS": 0x2D,
        "DELETE": 0x2E, "DEL": 0x2E,
        "HOME": 0x24, "END": 0x23,
        "PAGEUP": 0x21, "PGUP": 0x21,
        "PAGEDOWN": 0x22, "PGDOWN": 0x22,
        "TAB": 0x09, "SPACE": 0x20,
        "BACKSPACE": 0x08,
        "RETURN": 0x0D, "ENTER": 0x0D,
        "ESCAPE": 0x1B, "ESC": 0x1B,
        "UP": 0x26, "DOWN": 0x28,
        "LEFT": 0x25, "RIGHT": 0x27,
    }
    return special.get(key)


def parse_key_sequence(seq_str: str) -> tuple[int, int] | None:
    """'Ctrl+Shift+1' → (MOD_CTRL|MOD_SHIFT, 0x31). 실패 시 None."""
    if not seq_str:
        return None
    parts = [p.strip() for p in seq_str.split("+")]
    mods = 0
    key: str | None = None
    for p in parts:
        up = p.upper()
        if up == "CTRL":
            mods |= MOD_CONTROL
        elif up == "SHIFT":
            mods |= MOD_SHIFT
        elif up == "ALT":
            mods |= MOD_ALT
        elif up in ("META", "WIN"):
            mods |= MOD_WIN
        elif up:
            key = p
    if key is None:
        return None
    vk = _vk_for_key(key)
    if vk is None:
        return None
    return mods, vk


def make_camera_icon() -> QIcon:
    """카메라 모양 트레이 아이콘 (PNG 파일 없이 코드로 그림)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    # 카메라 본체
    p.setBrush(QColor("#0078D4"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 18, 56, 40, 7, 7)
    # 상단 뷰파인더
    p.drawRoundedRect(22, 10, 20, 12, 3, 3)
    # 렌즈 (흰 테)
    p.setBrush(Qt.white)
    p.drawEllipse(20, 23, 24, 24)
    # 렌즈 내부
    p.setBrush(QColor("#0078D4"))
    p.drawEllipse(26, 29, 12, 12)
    # 셔터 하이라이트
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(28, 31, 3, 3)
    p.end()
    return QIcon(pix)


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("화면 캡쳐")
        self.setWindowIcon(make_camera_icon())
        self.resize(320, 270)

        self.save_dir: Path = Path.home() / "Pictures" / "CaptureApp"
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self._settings = load_settings()
        self._floating: list[FloatingImage] = []
        self._selector: RegionSelector | None = None
        self._was_visible = True
        self._tray_hint_shown = False
        self._really_quit = False
        # 마우스 클릭으로 활성창 캡쳐 시 포어그라운드가 작업표시줄로 넘어가는
        # 문제를 막기 위해, 사용자가 마지막으로 쓰던 '실제' 창을 추적해 둔다.
        self._last_active_hwnd = None

        self._build_ui()
        self._build_tray()
        self._install_hotkeys()
        self._install_foreground_tracker()

        # 플로팅 창 '밖' 우클릭으로 닫기 위한 전역 우클릭 감시.
        # 플로팅 창이 하나라도 떠 있을 때만 켠다.
        self._rclick_watcher = GlobalRightClickWatcher()
        self._rclick_watcher.right_pressed.connect(self._on_global_right_click)

        # 캡쳐를 붙여넣는 순간(Ctrl+V) 해당 플로팅 창을 닫기 위한 감시.
        # 클립보드 시퀀스 번호로 "아직 그 캡쳐가 클립보드에 있는지" 확인한다.
        self._paste_watcher = GlobalPasteWatcher()
        self._paste_watcher.paste_pressed.connect(self._on_global_paste)
        self._clip_fw: FloatingImage | None = None  # 클립보드 내용과 짝인 플로팅 창
        self._clip_seq: int | None = None  # 그 시점의 클립보드 시퀀스 번호

    # ---- UI ----
    def _build_ui(self) -> None:
        self.btn_region = QPushButton("영역 캡쳐")
        self.btn_full = QPushButton("전체화면 캡쳐")
        self.btn_active = QPushButton("활성창 캡쳐")
        for b in (self.btn_region, self.btn_full, self.btn_active):
            b.setMinimumHeight(38)
            b.setStyleSheet("font-size: 12px; text-align: left; padding-left: 14px;")

        self.cb_clipboard = QCheckBox("클립보드 자동 복사")
        self.cb_save = QCheckBox("파일 자동 저장")
        self.cb_float = QCheckBox("캡쳐 후 화면에 띄우기")
        self.cb_autostart = QCheckBox("Windows 시작 시 자동 실행 (트레이로)")
        self.cb_clipboard.setChecked(True)
        self.cb_save.setChecked(True)
        self.cb_float.setChecked(True)
        self.cb_autostart.setChecked(autostart.is_enabled())
        self.cb_autostart.toggled.connect(self._on_autostart_toggled)

        path_row = QHBoxLayout()
        self.lbl_path = QLabel(str(self.save_dir))
        self.lbl_path.setStyleSheet("color: #666; font-size: 11px;")
        self.lbl_path.setWordWrap(True)
        btn_pick = QPushButton("...")
        btn_pick.setMaximumWidth(32)
        btn_pick.clicked.connect(self._change_path)
        btn_open = QPushButton("열기")
        btn_open.setMaximumWidth(48)
        btn_open.clicked.connect(self._open_folder)
        path_row.addWidget(self.lbl_path, 1)
        path_row.addWidget(btn_pick)
        path_row.addWidget(btn_open)

        layout = QVBoxLayout(self)
        layout.addWidget(self.btn_region)
        layout.addWidget(self.btn_full)
        layout.addWidget(self.btn_active)
        layout.addSpacing(6)
        layout.addWidget(self.cb_float)
        layout.addWidget(self.cb_clipboard)
        layout.addWidget(self.cb_save)
        layout.addWidget(self.cb_autostart)
        layout.addLayout(path_row)
        layout.addWidget(self._build_hotkey_group())

        self.btn_region.clicked.connect(self._capture_region)
        self.btn_full.clicked.connect(self._capture_full)
        self.btn_active.clicked.connect(self._capture_active)

    def _build_hotkey_group(self) -> QGroupBox:
        group = QGroupBox("전역 단축키 (포커스 후 키 입력)")
        v = QVBoxLayout(group)
        v.setSpacing(4)

        saved = self._settings.get("hotkeys", {})
        self.hk_edits: dict[str, QKeySequenceEdit] = {}
        for key in HK_KEYS:
            row = QHBoxLayout()
            lbl = QLabel(HK_LABEL[key])
            lbl.setMinimumWidth(70)
            seq_str = saved.get(key, DEFAULT_HOTKEYS[key])
            edit = QKeySequenceEdit(QKeySequence(seq_str))
            try:
                edit.setMaximumSequenceLength(1)  # 한 조합만 (Qt 6.5+)
            except AttributeError:
                pass
            edit.editingFinished.connect(self._reapply_hotkeys)
            btn_reset = QPushButton("기본")
            btn_reset.setMaximumWidth(50)
            btn_reset.clicked.connect(
                lambda _, e=edit, d=DEFAULT_HOTKEYS[key]: (
                    e.setKeySequence(QKeySequence(d)),
                    self._reapply_hotkeys(),
                )
            )
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(btn_reset)
            v.addLayout(row)
            self.hk_edits[key] = edit
        return group

    def _update_button_labels(self) -> None:
        """현재 등록된 단축키를 캡쳐 버튼 라벨에도 반영."""
        mapping = {
            "region": self.btn_region,
            "full": self.btn_full,
            "active": self.btn_active,
        }
        for key, btn in mapping.items():
            seq = self.hk_edits[key].keySequence().toString(
                QKeySequence.PortableText
            )
            base = HK_LABEL[key]
            btn.setText(f"{base}   ({seq})" if seq else base)

    # ---- Tray ----
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_camera_icon(), self)
        self.tray.setToolTip("화면 캡쳐  (좌/우클릭: 메뉴 / 더블클릭: 영역 캡쳐)")

        menu = QMenu()
        self._tray_actions: dict[str, QAction] = {}
        for key in HK_KEYS:
            act = QAction(HK_LABEL[key], self)
            handler = {
                "region": self._capture_region,
                "full": self._capture_full,
                "active": self._capture_active,
            }[key]
            act.triggered.connect(handler)
            menu.addAction(act)
            self._tray_actions[key] = act
        menu.addSeparator()

        a_show = QAction("메인 창 보이기", self)
        a_show.triggered.connect(self._show_main)
        menu.addAction(a_show)

        a_open = QAction("저장 폴더 열기", self)
        a_open.triggered.connect(self._open_folder)
        menu.addAction(a_open)
        menu.addSeparator()

        a_quit = QAction("종료", self)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        # 좌/우/중클릭 모두 메뉴 표시 (단 Windows에선 우클릭은 Qt가 자동 처리)
        if reason in (
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.MiddleClick,
        ):
            menu = self.tray.contextMenu()
            if menu is not None:
                menu.popup(QCursor.pos())
        elif reason == QSystemTrayIcon.DoubleClick:
            self._capture_region()

    def _update_tray_labels(self) -> None:
        for key, act in self._tray_actions.items():
            seq = self.hk_edits[key].keySequence().toString(
                QKeySequence.PortableText
            )
            base = HK_LABEL[key]
            act.setText(f"{base}   {seq}" if seq else base)

    # ---- Hotkeys ----
    def _install_hotkeys(self) -> None:
        self.hotkeys = GlobalHotkeys()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.register_failed.connect(self._on_hotkey_register_failed)
        self._reapply_hotkeys()

    def _on_hotkey(self, hid: int) -> None:
        if hid == HK_REGION:
            self._capture_region()
        elif hid == HK_FULL:
            self._capture_full()
        elif hid == HK_ACTIVE:
            self._capture_active()

    def _reapply_hotkeys(self) -> None:
        """현재 단축키 편집 위젯의 값을 읽어 설정 저장 + 전역 핫키 재등록."""
        bindings: dict[int, tuple[int, int]] = {}
        hk_save: dict[str, str] = {}
        invalid: list[str] = []
        for key in HK_KEYS:
            seq = self.hk_edits[key].keySequence().toString(
                QKeySequence.PortableText
            )
            hk_save[key] = seq
            parsed = parse_key_sequence(seq)
            if parsed:
                bindings[HK_ID[key]] = parsed
            elif seq:
                invalid.append(f"{HK_LABEL[key]}({seq})")

        # 저장
        self._settings.setdefault("hotkeys", {}).update(hk_save)
        save_settings(self._settings)

        # 버튼/메뉴 라벨 갱신
        self._update_button_labels()
        if hasattr(self, "_tray_actions"):
            self._update_tray_labels()

        # 전역 핫키 재등록
        self.hotkeys.set_bindings(bindings)

        if invalid and hasattr(self, "tray"):
            self.tray.showMessage(
                "단축키 파싱 실패",
                "다음 단축키는 형식이 올바르지 않아 등록되지 않았어요: "
                + ", ".join(invalid),
                QSystemTrayIcon.Warning,
                4000,
            )

    def _on_hotkey_register_failed(self, hid: int, desc: str) -> None:
        # ID → 사람이 읽는 이름
        name = next(
            (HK_LABEL[k] for k, v in HK_ID.items() if v == hid),
            str(hid),
        )
        msg = (
            f"{name} 단축키 등록 실패 ({desc}). "
            "다른 앱이 같은 조합을 쓰고 있을 수 있어요."
        )
        print(f"[hotkey] {msg}", file=sys.stderr)
        if hasattr(self, "tray"):
            self.tray.showMessage(
                "단축키 등록 실패", msg, QSystemTrayIcon.Warning, 4000
            )

    # ---- 활성창(포어그라운드) 추적 ----
    def _install_foreground_tracker(self) -> None:
        self._fg_timer = QTimer(self)
        self._fg_timer.setInterval(200)
        self._fg_timer.timeout.connect(self._track_foreground)
        self._fg_timer.start()

    def _track_foreground(self) -> None:
        """현재 포어그라운드 창을 주기적으로 기록한다.

        캡쳐 앱 자신의 창(PID 비교)과 작업표시줄·바탕화면 같은 셸 창
        (클래스명 비교)은 제외한다. 그래야 마우스 클릭으로 활성창 캡쳐를
        실행해 포어그라운드가 작업표시줄로 넘어가더라도, 직전에 쓰던
        실제 창을 대상으로 잡을 수 있다.
        """
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd or not _user32.IsWindowVisible(hwnd):
                return
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return  # 우리 앱 창(메인/플로팅/영역선택) 제외
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in _SHELL_WINDOW_CLASSES:
                return  # 작업표시줄/바탕화면 등 셸 창 제외
            self._last_active_hwnd = hwnd
        except Exception:
            pass  # 추적 실패는 캡쳐 동작에 영향 주지 않도록 조용히 무시

    # ---- handlers ----
    def _change_path(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", str(self.save_dir))
        if d:
            self.save_dir = Path(d)
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.lbl_path.setText(str(self.save_dir))

    def _on_autostart_toggled(self, checked: bool) -> None:
        try:
            autostart.set_enabled(checked)
        except Exception as exc:
            print(f"[autostart] {exc}", file=sys.stderr)
            # 실패 시 체크 상태 되돌리기 (시그널 재귀 방지)
            self.cb_autostart.blockSignals(True)
            self.cb_autostart.setChecked(autostart.is_enabled())
            self.cb_autostart.blockSignals(False)

    def _open_folder(self) -> None:
        try:
            os.startfile(self.save_dir)  # Windows
        except Exception as exc:
            print(f"[open folder] {exc}", file=sys.stderr)

    def _show_main(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- capture flow ----
    def _capture_region(self) -> None:
        self._was_visible = self.isVisible()
        if self._was_visible:
            self.hide()
        QTimer.singleShot(180, self._show_selector)

    def _show_selector(self) -> None:
        self._selector = RegionSelector()
        self._selector.selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._restore_main)
        self._selector.show()
        self._selector.activateWindow()
        self._selector.raise_()

    def _on_region_selected(self, rect) -> None:
        # rect: logical global 좌표. Qt가 모니터별 DPI를 알아서 처리.
        try:
            img = grab_logical_region(rect)
        except Exception as exc:
            print(f"[capture] region error: {exc}", file=sys.stderr)
            self._restore_main()
            return
        self._process(img, origin=rect.topLeft())

    def _capture_full(self) -> None:
        self._was_visible = self.isVisible()
        if self._was_visible:
            self.hide()
        QTimer.singleShot(220, self._do_full)

    def _do_full(self) -> None:
        try:
            img = grab_fullscreen()
        except Exception as exc:
            print(f"[capture] fullscreen error: {exc}", file=sys.stderr)
            self._restore_main()
            return
        self._process(img)

    def _capture_active(self) -> None:
        self._was_visible = self.isVisible()
        if self._was_visible:
            self.hide()
        QTimer.singleShot(220, self._do_active)

    def _do_active(self) -> None:
        try:
            # 추적해 둔 '마지막 실제 창'을 우선 사용. 없으면 현재 포어그라운드.
            hwnd = self._last_active_hwnd
            x, y, w, h = window_rect(hwnd) if hwnd else active_window_rect()
            if w <= 0 or h <= 0:
                img = grab_fullscreen()
                origin = None
            else:
                img = grab_region(x, y, w, h)
                dpr = QGuiApplication.primaryScreen().devicePixelRatio()
                origin = QPoint(int(round(x / dpr)), int(round(y / dpr)))
        except Exception as exc:
            print(f"[capture] active window error: {exc}", file=sys.stderr)
            self._restore_main()
            return
        self._process(img, origin=origin)

    # ---- post-capture ----
    def _process(self, img, origin: QPoint | None = None) -> None:
        copied_now = False
        if self.cb_clipboard.isChecked():
            QApplication.clipboard().setImage(pil_to_clipboard_image(img))
            copied_now = True

        if self.cb_save.isChecked():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.save_dir / f"capture_{ts}.png"
            try:
                save_pil_with_dpi(img, path)
            except Exception as exc:
                print(f"[capture] save error: {exc}", file=sys.stderr)

        if self.cb_float.isChecked():
            fw = FloatingImage(img)
            self._place_floating(fw, origin)
            fw.destroyed.connect(lambda *_: self._on_floating_destroyed(fw))
            fw.copied.connect(lambda fw=fw: self._register_clip_target(fw))
            self._floating.append(fw)
            fw.show()
            if copied_now:
                self._register_clip_target(fw)
            self._rclick_watcher.start()  # 창 밖 우클릭 감시 시작
            self._paste_watcher.start()  # 붙여넣기(Ctrl+V) 감시 시작

        self._restore_main()

    def _register_clip_target(self, fw: FloatingImage) -> None:
        """클립보드에 담긴 캡쳐와 플로팅 창을 짝지어 둔다.

        이후 Ctrl+V 시점에 클립보드가 그대로면(시퀀스 번호 동일) 이 창을 닫는다.
        """
        self._clip_fw = fw
        self._clip_seq = int(_user32.GetClipboardSequenceNumber())

    def _on_global_paste(self) -> None:
        fw = self._clip_fw
        if fw is None or fw not in self._floating:
            return
        # 캡쳐 후 다른 내용을 복사했다면(클립보드가 바뀜) 닫지 않는다.
        if int(_user32.GetClipboardSequenceNumber()) != self._clip_seq:
            return
        self._clip_fw = None
        fw.close()

    def _on_floating_destroyed(self, fw) -> None:
        if fw in self._floating:
            self._floating.remove(fw)
        if self._clip_fw is fw:
            self._clip_fw = None
        if not self._floating:
            self._rclick_watcher.stop()  # 남은 플로팅 없으면 감시 종료
            self._paste_watcher.stop()

    def _on_global_right_click(self, px: int, py: int) -> None:
        """전역 우클릭 물리 좌표(px, py) 기준으로, 그 점이 '밖'인
        플로팅 창을 닫는다. 점이 안에 있는 창은 기존 우클릭 메뉴가 뜬다.
        """
        user32 = ctypes.windll.user32
        for fw in list(self._floating):
            try:
                rect = wintypes.RECT()
                if not user32.GetWindowRect(int(fw.winId()), ctypes.byref(rect)):
                    continue
                inside = (
                    rect.left <= px < rect.right and rect.top <= py < rect.bottom
                )
                if not inside:
                    fw.close()
            except Exception:
                pass

    def _place_floating(
        self, fw: FloatingImage, origin: QPoint | None = None
    ) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.8)
        max_h = int(screen.height() * 0.8)
        scaled = False
        if fw.width() > max_w or fw.height() > max_h:
            ratio = min(max_w / fw.width(), max_h / fw.height())
            fw._scale = ratio  # noqa: SLF001
            fw._render()  # noqa: SLF001
            scaled = True

        if origin is not None and not scaled:
            # 캡쳐된 위치에 그대로 띄움 (테두리 2px 보정)
            fw.move(origin.x() - 2, origin.y() - 2)
            return

        # 위치 정보가 없거나, 너무 커서 축소된 경우엔 화면 중앙에 살짝 비스듬히
        offset = 24 * len(self._floating)
        cx = screen.center().x() - fw.width() // 2 + offset
        cy = screen.center().y() - fw.height() // 2 + offset
        fw.move(cx, cy)

    def _restore_main(self) -> None:
        if self._was_visible:
            self._show_main()

    # ---- close / quit ----
    def closeEvent(self, e) -> None:
        if self._really_quit or not self.tray.isVisible():
            # 정말 종료
            for fw in list(self._floating):
                fw.close()
            try:
                self._fg_timer.stop()
            except Exception:
                pass
            try:
                self._rclick_watcher.stop()
            except Exception:
                pass
            try:
                self._paste_watcher.stop()
            except Exception:
                pass
            try:
                self.hotkeys.stop()
            except Exception:
                pass
            self.tray.hide()
            super().closeEvent(e)
            QApplication.quit()
            return

        # X 클릭은 트레이로 최소화
        if not self._tray_hint_shown:
            self.tray.showMessage(
                "화면 캡쳐",
                "트레이로 숨겼어요. 트레이 좌클릭으로 다시 열거나, 단축키로 캡쳐하세요.",
                QSystemTrayIcon.Information,
                3500,
            )
            self._tray_hint_shown = True
        self.hide()
        e.ignore()

    def _quit(self) -> None:
        self._really_quit = True
        self.close()


def _set_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def main() -> None:
    _set_dpi_awareness()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 트레이만 남아도 살아있어야 핫키 동작
    w = MainWindow()
    # 메인 창은 시작 시 띄우지 않음 — 트레이 아이콘으로만 시작
    # --show 인자가 있을 때만 메인 창 표시 (개발/디버깅용)
    if "--show" in sys.argv:
        w.show()

    # 첫 실행 시에만 트레이 안내 (이후엔 조용히 시작)
    marker = w.save_dir / ".launched"
    if not marker.exists():
        try:
            marker.touch()
        except Exception:
            pass
        QTimer.singleShot(
            600,
            lambda: w.tray.showMessage(
                "화면 캡쳐 실행 중",
                "트레이 아이콘에서 메뉴를 열거나, Ctrl+Shift+1/2/3 으로 바로 캡쳐하세요.",
                QSystemTrayIcon.Information,
                5000,
            ),
        )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
