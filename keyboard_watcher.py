"""전역 Ctrl+V(붙여넣기) 감시 — WH_KEYBOARD_LL 저수준 키보드 훅 (의존성 없음).

캡쳐를 다른 앱에 붙여넣는 순간 플로팅 창을 닫기 위해, 시스템 전역의
Ctrl+V 키 다운을 감지해 Qt 시그널로 알린다. mouse_watcher 와 같은
패턴(별도 스레드 + 메시지 루프 + Qt 시그널)이다.

- 이벤트는 삼키지 않고 그대로 통과시킨다(비차단).
- Ctrl+V 외의 키는 아무것도 기록·전달하지 않는다.
- 훅은 플로팅 창이 떠 있는 동안에만 켠다 (start/stop 은 호출자 책임).
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
VK_V = 0x56
VK_CONTROL = 0x11

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


class GlobalPasteWatcher(QObject):
    """전역 Ctrl+V 키 다운 발생 시 emit."""

    paste_pressed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._hook = None
        self._proc = None  # 콜백 참조 유지 (GC 방지)
        self._stop = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._tid is not None:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._tid = None

    # ---- internal ----
    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()

        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
        ]
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        def proc(nCode, wParam, lParam):
            try:
                if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    kb = ctypes.cast(
                        lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)
                    ).contents
                    if kb.vkCode == VK_V and (
                        user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                    ):
                        self.paste_pressed.emit()
            except Exception:
                pass  # 콜백은 무슨 일이 있어도 다음 훅으로 넘겨야 함
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._proc = HOOKPROC(proc)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0
        )
        if not self._hook:
            self._tid = None
            return

        msg = _MSG()
        try:
            while not self._stop:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
