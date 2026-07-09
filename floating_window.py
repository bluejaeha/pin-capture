"""캡쳐 결과를 화면에 떠있게 보여주는 창 (GTPicThis 스타일)."""
from __future__ import annotations

from datetime import datetime

from PIL import Image
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMenu,
)


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL Image → QImage (사본 반환, 메모리 안전)."""
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888)
    return qimg.copy()


def pil_to_clipboard_image(img: Image.Image) -> QImage:
    """클립보드용 QImage.

    엑셀은 클립보드 DIB의 DPI 정보를 무시하고 픽셀 단위로 그린다.
    따라서 화면에서 보이는 크기(=logical pixel)로 미리 리사이즈해서 넣어야
    어디에 붙여넣어도 캡쳐한 크기 그대로 들어간다.
    """
    from PySide6.QtGui import QGuiApplication

    dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
    if abs(dpr - 1.0) > 0.01:
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS  # 구버전 Pillow 호환
        logical_w = max(1, int(round(img.width / dpr)))
        logical_h = max(1, int(round(img.height / dpr)))
        target = img.resize((logical_w, logical_h), resample)
    else:
        target = img

    qimg = pil_to_qimage(target)
    # 96 DPI 정보도 박아둠 (DPI를 존중하는 프로그램용)
    dpm = int(round(96 / 0.0254))
    qimg.setDotsPerMeterX(dpm)
    qimg.setDotsPerMeterY(dpm)
    return qimg


def save_pil_with_dpi(img: Image.Image, path) -> None:
    """PNG/JPG에 DPI 정보 포함해 저장."""
    from PySide6.QtGui import QGuiApplication

    dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
    dpi = (int(round(96 * dpr)), int(round(96 * dpr)))
    img.save(path, dpi=dpi)


class FloatingImage(QLabel):
    copied = Signal()  # 이 창의 이미지를 클립보드에 복사했을 때

    def __init__(self, pil_image: Image.Image) -> None:
        super().__init__()
        self.pil_image = pil_image
        self._scale = 1.0

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(
            "QLabel { border: 2px solid #00C8FF; background: #111; }"
        )
        self.setToolTip(
            "드래그: 이동  |  휠: 확대/축소  |  더블클릭: 닫기  |  우클릭: 메뉴"
        )
        self._render()
        self._drag_offset: QPoint | None = None

    # ---- rendering ----
    def _render(self) -> None:
        qimg = pil_to_qimage(self.pil_image)
        # 화면 DPI 배율을 반영해야 캡쳐한 원본 크기 그대로 보임
        dpr = self.devicePixelRatioF() or 1.0
        logical_w = max(1, int(self.pil_image.width / dpr * self._scale))
        logical_h = max(1, int(self.pil_image.height / dpr * self._scale))
        pix = QPixmap.fromImage(qimg).scaled(
            int(logical_w * dpr),
            int(logical_h * dpr),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        pix.setDevicePixelRatio(dpr)
        self.setPixmap(pix)
        self.setFixedSize(logical_w, logical_h)

    # ---- mouse: drag / wheel / dblclick ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, _e):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, _e):
        self.close()

    def wheelEvent(self, e):
        center = self.frameGeometry().center()
        if e.angleDelta().y() > 0:
            self._scale = min(self._scale * 1.1, 8.0)
        else:
            self._scale = max(self._scale * 0.9, 0.1)
        self._render()
        new_geo = self.frameGeometry()
        new_geo.moveCenter(center)
        self.move(new_geo.topLeft())

    # ---- keyboard ----
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() == Qt.Key_C and e.modifiers() & Qt.ControlModifier:
            self.copy_to_clipboard()
        elif e.key() == Qt.Key_S and e.modifiers() & Qt.ControlModifier:
            self.save_as()
        elif e.key() == Qt.Key_0:
            self._scale = 1.0
            self._render()

    # ---- context menu ----
    def contextMenuEvent(self, e):
        menu = QMenu(self)
        a_copy = menu.addAction("클립보드에 복사  (Ctrl+C)")
        a_save = menu.addAction("다른 이름으로 저장...  (Ctrl+S)")
        menu.addSeparator()
        a_reset = menu.addAction("원본 크기  (0)")
        a_close = menu.addAction("닫기  (Esc)")
        chosen = menu.exec(e.globalPos())
        if chosen == a_copy:
            self.copy_to_clipboard()
        elif chosen == a_save:
            self.save_as()
        elif chosen == a_reset:
            self._scale = 1.0
            self._render()
        elif chosen == a_close:
            self.close()

    # ---- actions ----
    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setImage(pil_to_clipboard_image(self.pil_image))
        self.copied.emit()

    def save_as(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "이미지 저장",
            f"capture_{ts}.png",
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)",
        )
        if path:
            save_pil_with_dpi(self.pil_image, path)
