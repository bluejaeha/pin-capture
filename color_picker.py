"""화면 어디서나 픽셀 색을 집는 스포이드.

멀티모니터에서 모니터별 DPI 배율이 다르면, 여러 모니터를 한 창으로 덮을 때
Qt가 한 배율로만 렌더링해 보조 모니터 쪽 스냅샷이 어긋난다.
그래서 **모니터마다 별도 오버레이 창**을 띄운다. 각 창은 자기 모니터 안에만
있으므로 그 모니터의 DPI로 정확히 그려진다.

색은 모니터별 스냅샷(`grab_logical_region`, 물리 픽셀 보정)에서 읽어 정확하다.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from capture import grab_logical_region


def _pil_to_qimage(img) -> QImage:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888)
    return q.copy()


class _PickerOverlay(QWidget):
    """모니터 한 대를 덮는 스포이드 오버레이."""

    def __init__(self, screen, on_pick, on_cancel) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._on_cancel = on_cancel

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._geo = screen.geometry()          # 이 모니터의 logical global 좌표
        self._dpr = screen.devicePixelRatio() or 1.0
        try:
            self.setScreen(screen)             # 이 모니터에 생성되도록 힌트
        except Exception:
            pass
        self.setGeometry(self._geo)

        try:
            self._pil = grab_logical_region(self._geo)
        except Exception:
            self._pil = None
        self._qpix = (
            QPixmap.fromImage(_pil_to_qimage(self._pil)) if self._pil else None
        )

        self._cursor = QPoint(-100, -100)
        self._rgb = (0, 0, 0)

    # ---- 색 읽기 (로컬 logical → 물리 픽셀) ----
    def _read(self, local_pt: QPoint) -> tuple[int, int, int]:
        if self._pil is None:
            return self._rgb
        rx = int(local_pt.x() * self._dpr)
        ry = int(local_pt.y() * self._dpr)
        rx = min(max(rx, 0), self._pil.width - 1)
        ry = min(max(ry, 0), self._pil.height - 1)
        px = self._pil.getpixel((rx, ry))
        return (int(px[0]), int(px[1]), int(px[2]))

    # ---- mouse / keyboard ----
    def mouseMoveEvent(self, e):
        self._cursor = e.position().toPoint()
        self._rgb = self._read(self._cursor)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on_pick(self._read(e.position().toPoint()))
        elif e.button() == Qt.RightButton:
            self._on_cancel()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._on_cancel()

    # ---- paint ----
    def paintEvent(self, _e):
        p = QPainter(self)
        if self._qpix is not None:
            p.drawPixmap(self.rect(), self._qpix)  # 이 모니터 스냅샷을 그대로 덮음

        c = self._cursor
        if c.x() < 0:
            return
        r, g, b = self._rgb
        hexs = f"#{r:02X}{g:02X}{b:02X}"

        panel = QRect(c.x() + 18, c.y() + 18, 168, 46)
        if panel.right() > self.width():
            panel.moveLeft(c.x() - 18 - panel.width())
        if panel.bottom() > self.height():
            panel.moveTop(c.y() - 18 - panel.height())

        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(25, 25, 25, 235))
        p.drawRoundedRect(panel, 6, 6)

        sw = QRect(panel.x() + 8, panel.y() + 9, 28, 28)
        p.setBrush(QColor(r, g, b))
        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
        p.drawRect(sw)

        p.setPen(Qt.white)
        f = QFont()
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.drawText(
            QRect(sw.right() + 8, panel.y() + 6, 120, 18),
            Qt.AlignVCenter | Qt.AlignLeft,
            hexs,
        )
        p.setPen(QColor(190, 190, 190))
        f2 = QFont()
        f2.setPixelSize(10)
        p.setFont(f2)
        p.drawText(
            QRect(sw.right() + 8, panel.y() + 26, 130, 14),
            Qt.AlignVCenter | Qt.AlignLeft,
            "클릭:복사  ESC/우클릭:취소",
        )


class ColorPicker(QObject):
    """모니터별 오버레이를 총괄하는 컨트롤러."""

    picked = Signal(int, int, int)  # r, g, b
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._done = False
        self._overlays: list[_PickerOverlay] = []
        for screen in QGuiApplication.screens():
            ov = _PickerOverlay(screen, self._handle_pick, self._handle_cancel)
            self._overlays.append(ov)

    def show(self) -> None:
        for ov in self._overlays:
            ov.show()
            ov.raise_()
        # 현재 커서가 있는 모니터의 오버레이에 키보드 포커스 (ESC용)
        cur = QCursor.pos()
        target = next(
            (ov for ov in self._overlays if ov._geo.contains(cur)),
            self._overlays[0] if self._overlays else None,
        )
        if target is not None:
            target.activateWindow()
            target.setFocus()

    def _close_all(self) -> None:
        for ov in self._overlays:
            ov.close()
        self._overlays = []

    def _handle_pick(self, rgb) -> None:
        if self._done:
            return
        self._done = True
        self._close_all()
        self.picked.emit(int(rgb[0]), int(rgb[1]), int(rgb[2]))

    def _handle_cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._close_all()
        self.cancelled.emit()
