"""전역 우클릭 감시 — WH_MOUSE_LL 저수준 마우스 훅 (의존성 없음).

플로팅 창 '밖'에서 우클릭하면 그 창을 닫기 위해, 화면 어디서든 우클릭
(버튼 다운)을 감지해 물리 화면 좌표를 Qt 시그널로 메인 스레드에 전달한다.
훅 콜백은 별도 스레드의 메시지 루프에서 돌고, 실제 창 조작(닫기)은
시그널을 받는 메인 스레드에서 한다.

훅은 시스템 전체 마우스 이벤트를 지나가므로, 플로팅 창이 하나라도 떠
있을 때만 start() 하고 모두 닫히면 stop() 하는 식으로 최소한만 켠다.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

WH_MOUSE_LL = 14
WM_RBUTTONDOWN = 0x0204
WM_QUIT = 0x0012

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", wintypes.DWORD),
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


class GlobalRightClickWatcher(QObject):
    """전역 우클릭(버튼 다운) 발생 시 물리 화면 좌표를 emit."""

    right_pressed = Signal(int, int)  # 물리 화면 좌표 (x, y)

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

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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
                if nCode >= 0 and wParam == WM_RBUTTONDOWN:
                    ms = ctypes.cast(
                        lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)
                    ).contents
                    self.right_pressed.emit(int(ms.pt.x), int(ms.pt.y))
            except Exception:
                pass  # 콜백은 무슨 일이 있어도 다음 훅으로 넘겨야 함
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._proc = HOOKPROC(proc)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0
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
