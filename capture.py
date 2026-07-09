"""화면 캡쳐 함수 모음."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from datetime import datetime
from io import BytesIO

import mss
from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect
from PySide6.QtGui import QGuiApplication, QImage


# 디버그 로그 — 필요할 때 _LOG_ENABLED = True 로 켜기. 로그는 ~/capture_debug.log
_LOG_ENABLED = False
_LOG_PATH = os.path.join(os.path.expanduser("~"), "capture_debug.log")


def _log(*args) -> None:
    if not _LOG_ENABLED:
        return
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                datetime.now().strftime("%H:%M:%S ")
                + " ".join(str(a) for a in args)
                + "\n"
            )
    except Exception:
        pass


def _shot_to_pil(shot) -> Image.Image:
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def grab_region(x: int, y: int, w: int, h: int) -> Image.Image:
    with mss.mss() as sct:
        mon = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
        return _shot_to_pil(sct.grab(mon))


def grab_fullscreen() -> Image.Image:
    """모든 모니터를 합친 가상 화면 전체."""
    with mss.mss() as sct:
        return _shot_to_pil(sct.grab(sct.monitors[0]))


def grab_primary() -> Image.Image:
    """주 모니터만."""
    with mss.mss() as sct:
        return _shot_to_pil(sct.grab(sct.monitors[1]))


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """주어진 창 핸들의 화면 좌표(물리 픽셀, 그림자 제외).

    창이 없거나 좌표를 얻지 못하면 (0, 0, 0, 0) 을 반환한다.
    """
    if not hwnd:
        return 0, 0, 0, 0
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    rect = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    res = dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if res != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0, 0, 0, 0
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def active_window_rect() -> tuple[int, int, int, int]:
    """포어그라운드 창의 화면 좌표(물리 픽셀, 그림자 제외)."""
    return window_rect(ctypes.windll.user32.GetForegroundWindow())


def grab_active_window() -> Image.Image:
    x, y, w, h = active_window_rect()
    if w <= 0 or h <= 0:
        return grab_primary()
    return grab_region(x, y, w, h)


# ---- Qt 기반 캡쳐 (멀티 모니터 + 모니터별 DPI 다른 환경에 안전) ----
def _qimage_to_pil(qimg: QImage) -> Image.Image:
    """QImage → PIL.Image. PNG 경유라 stride/format 문제 없이 안전."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimg.save(buf, "PNG")
    buf.close()
    return Image.open(BytesIO(bytes(ba))).convert("RGB")


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _enum_windows_monitors() -> list[dict]:
    """모든 monitor의 physical bounds + dpr + device name."""
    results: list[dict] = []

    proto = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def cb(hmonitor, hdc, rect, lparam):
        try:
            mi = _MONITORINFOEX()
            mi.cbSize = ctypes.sizeof(_MONITORINFOEX)
            if not ctypes.windll.user32.GetMonitorInfoW(
                hmonitor, ctypes.byref(mi)
            ):
                return 1
            x_dpi = ctypes.c_uint(96)
            y_dpi = ctypes.c_uint(96)
            try:
                ctypes.windll.shcore.GetDpiForMonitor(
                    hmonitor, 0, ctypes.byref(x_dpi), ctypes.byref(y_dpi)
                )
            except Exception:
                pass
            results.append(
                {
                    "device": mi.szDevice,
                    "left": mi.rcMonitor.left,
                    "top": mi.rcMonitor.top,
                    "right": mi.rcMonitor.right,
                    "bottom": mi.rcMonitor.bottom,
                    "dpr": x_dpi.value / 96.0,
                }
            )
        except Exception as exc:
            _log("[enum] error", exc)
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proto(cb), 0)
    return results


def grab_logical_region(rect: QRect) -> Image.Image:
    """Qt logical global 좌표를 받아 mss로 정확히 그 영역을 캡쳐.

    Qt screen.name()(예: \\\\.\\DISPLAY1)을 Windows API의 device name과
    매칭해서 그 모니터의 physical origin + dpr을 정확히 찾고,
    Qt의 logical 좌표를 physical 좌표로 변환해 mss에 넘긴다.
    """
    screen = QGuiApplication.screenAt(rect.center())
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    sg = screen.geometry()
    qt_name = screen.name()
    qt_dpr = screen.devicePixelRatio() or 1.0

    monitors = _enum_windows_monitors()
    # 1차 매칭: device 이름 (Qt가 device 이름을 반환하는 경우)
    target = next((m for m in monitors if m["device"] == qt_name), None)
    # 2차 매칭: geometry top-left 좌표
    #   (Qt가 모델명을 screen.name()으로 반환하는 보조 모니터에서 필요)
    if target is None:
        target = next(
            (m for m in monitors if m["left"] == sg.x() and m["top"] == sg.y()),
            None,
        )

    _log(
        f"rect=({rect.x()},{rect.y()},{rect.width()},{rect.height()})",
        f"screen={qt_name!r}",
        f"sg=({sg.x()},{sg.y()},{sg.width()},{sg.height()})",
        f"qt_dpr={qt_dpr}",
        f"target={target}",
        f"all_monitors={monitors}",
    )

    if target is not None:
        rel_x = rect.x() - sg.x()
        rel_y = rect.y() - sg.y()
        dpr = target["dpr"] or qt_dpr or 1.0
        px_x = target["left"] + int(round(rel_x * dpr))
        px_y = target["top"] + int(round(rel_y * dpr))
        px_w = max(1, int(round(rect.width() * dpr)))
        px_h = max(1, int(round(rect.height() * dpr)))
    else:
        # fallback: primary screen dpr
        px_x = int(round(rect.x() * qt_dpr))
        px_y = int(round(rect.y() * qt_dpr))
        px_w = max(1, int(round(rect.width() * qt_dpr)))
        px_h = max(1, int(round(rect.height() * qt_dpr)))

    _log(f"  -> mss bbox: x={px_x} y={px_y} w={px_w} h={px_h}")

    return grab_region(px_x, px_y, px_w, px_h)
