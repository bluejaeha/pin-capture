"""전역 단축키 — Windows RegisterHotKey API 직접 사용 (의존성 없음).

별도 스레드에서 RegisterHotKey + 메시지 루프를 돌리고, WM_HOTKEY를
받으면 Qt 시그널로 메인 스레드에 전달한다.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

# Windows 상수
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


class GlobalHotkeys(QObject):
    """핫키 ID -> Qt 시그널.

    사용:
        gh = GlobalHotkeys()
        gh.add(1, MOD_CONTROL | MOD_SHIFT, 0x31)  # Ctrl+Shift+1
        gh.triggered.connect(handler)
        gh.start()
        ...
        gh.stop()
    """

    triggered = Signal(int)
    register_failed = Signal(int, str)  # hid, description

    def __init__(self) -> None:
        super().__init__()
        self._bindings: dict[int, tuple[int, int]] = {}
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._stop = False

    def add(self, hid: int, mods: int, vk: int) -> None:
        self._bindings[hid] = (mods, vk)

    def set_bindings(self, bindings: dict[int, tuple[int, int]]) -> None:
        """모든 바인딩 교체. 이미 실행 중이면 중단 후 재시작."""
        was_running = self._thread is not None and self._thread.is_alive()
        if was_running:
            self.stop()
        self._bindings = dict(bindings)
        self.start()

    def start(self) -> None:
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._tid is not None:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    # ---- internal ----
    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()

        registered: list[int] = []
        for hid, (mods, vk) in self._bindings.items():
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                registered.append(hid)
            else:
                desc = f"mods=0x{mods:x} vk=0x{vk:x}"
                print(f"[hotkey] register failed: id={hid} {desc}")
                self.register_failed.emit(hid, desc)

        msg = _MSG()
        try:
            while not self._stop:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    self.triggered.emit(int(msg.wParam))
        finally:
            for hid in registered:
                user32.UnregisterHotKey(None, hid)


# 자주 쓰는 가상 키 코드
VK = {
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39, "0": 0x30,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "PRINTSCREEN": 0x2C,
}
