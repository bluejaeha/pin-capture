"""드래그로 화면 영역을 선택하는 반투명 오버레이."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionSelector(QWidget):
    selected = Signal(QRect)  # 글로벌 좌표 기준
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)

        # 모든 모니터를 덮도록
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)

        self._origin: QPoint | None = None  # 로컬 좌표
        self._current: QPoint | None = None

    # ---- mouse / keyboard ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._current = self._origin
            self.update()
        elif e.button() == Qt.RightButton:
            self.cancelled.emit()
            self.close()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._origin is not None:
            rect = QRect(self._origin, self._current).normalized()
            if rect.width() >= 3 and rect.height() >= 3:
                top_left_global = self.mapToGlobal(rect.topLeft())
                global_rect = QRect(top_left_global, rect.size())
                # selector를 먼저 화면에서 숨기고, 잠시 후 시그널 발생 + close.
                # 이렇게 안 하면 mss가 selector의 파란 선택 테두리까지 함께 캡쳐함.
                self.hide()
                QTimer.singleShot(80, lambda: (
                    self.selected.emit(global_rect), self.close()
                ))
            else:
                self.cancelled.emit()
                self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()

    # ---- paint ----
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self._origin is None or self._current is None:
            self._draw_hint(p)
            return

        rect = QRect(self._origin, self._current).normalized()
        # 선택 영역만 비우기
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(rect, Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        pen = QPen(QColor(0, 200, 255), 2)
        p.setPen(pen)
        p.drawRect(rect)

        # 크기 라벨
        label = f"{rect.width()} x {rect.height()}"
        p.setPen(Qt.white)
        label_pos = rect.bottomRight() + QPoint(6, 18)
        # 라벨이 화면 밖으로 나가지 않도록
        if label_pos.x() + 80 > self.width():
            label_pos.setX(rect.right() - 80)
        if label_pos.y() > self.height():
            label_pos.setY(rect.top() - 6)
        p.fillRect(label_pos.x() - 4, label_pos.y() - 14, 90, 18, QColor(0, 0, 0, 180))
        p.drawText(label_pos, label)

    def _draw_hint(self, p: QPainter) -> None:
        p.setPen(Qt.white)
        hint = "드래그해서 영역 선택  |  ESC / 우클릭 = 취소"
        p.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignTop, hint)
