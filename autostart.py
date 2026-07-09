"""Windows 시작 시 자동 실행 — HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 등록."""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CaptureApp"


def _autostart_command() -> str:
    """레지스트리에 넣을 실행 커맨드.

    - PyInstaller로 빌드된 .exe 환경: sys.executable이 곧 우리 앱 exe
    - 일반 파이썬 환경: pythonw.exe + main.py
    """
    if getattr(sys, "frozen", False):
        # PyInstaller bundled exe
        return f'"{sys.executable}"'

    py = Path(sys.executable)
    pyw = py.parent / "pythonw.exe"
    if not pyw.exists():
        pyw = py
    script = Path(__file__).resolve().parent / "main.py"
    return f'"{pyw}" "{script}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if enabled:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.DeleteValue(k, APP_NAME)
        except FileNotFoundError:
            pass


def current_command() -> str | None:
    """현재 등록된 커맨드(있으면). 디버깅/표시용."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return val
    except (FileNotFoundError, OSError):
        return None
