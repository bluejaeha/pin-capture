"""캡쳐 결과를 화면에 떠있게 보여주는 창 (GTPicThis 스타일).

플로팅 창 위에서 바로 주석(박스·화살표·펜·글자)을 그릴 수 있다.
주석은 이미지 픽셀 좌표로 저장되어, 확대/축소해도 함께 움직이고
복사(Ctrl+C)·저장(Ctrl+S) 시 이미지에 구워져 함께 출력된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from PIL import Image
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QWidget,
)


# 색상 팔레트 (클릭 순환). 빨강이 기본.
PRESET_COLORS = ["#FF3B30", "#FFCC00", "#007AFF", "#34C759", "#000000", "#FFFFFF"]
# 선 굵기 (클릭 순환)
PRESET_WIDTHS = [2, 4, 6, 8]


@dataclass
class Annotation:
    """한 개의 주석. 좌표(points)는 이미지 픽셀 기준.

    - rect / arrow: points = [(x0,y0), (x1,y1)]
    - pen:          points = [(x0,y0), (x1,y1), ...]
    - text:         points = [(x0,y0)] + text
    """

    kind: str          # 'rect' | 'arrow' | 'pen' | 'text'
    color: str         # '#RRGGBB'
    width: int         # 선 굵기(이미지 픽셀)
    points: list       # list[tuple[float, float]]
    text: str = ""


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL Image → QImage (사본 반환, 메모리 안전)."""
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888)
    return qimg.copy()


def qimage_to_pil(qimg: QImage) -> Image.Image:
    """QImage → PIL.Image (PNG 경유라 stride/format 문제 없이 안전)."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimg.save(buf, "PNG")
    buf.close()
    return Image.open(BytesIO(bytes(ba))).convert("RGB")


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
    copied = Signal()   # 이 창의 이미지를 클립보드에 복사했을 때
    changed = Signal()  # 주석이 추가/취소/삭제되어 이미지 내용이 바뀌었을 때

    def __init__(self, pil_image: Image.Image) -> None:
        super().__init__()
        self.pil_image = pil_image
        self._scale = 1.0

        # ---- 주석 상태 ----
        self._annotations: list[Annotation] = []
        self._draft: Annotation | None = None       # 그리는 중인 주석
        self._tool = "move"                          # 현재 도구
        self._color_idx = 0                          # PRESET_COLORS 인덱스
        self._width = 4                              # 선 굵기
        self._text_edit: QLineEdit | None = None     # 글자 입력 중인 위젯
        self._text_pt: QPointF | None = None
        self._text_committed = False
        self._drag_offset: QPoint | None = None

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
            "상단 툴바: 도구 선택  |  드래그: 그리기/이동  |  휠: 확대/축소  "
            "|  더블클릭: 닫기(이동 모드)  |  우클릭: 메뉴"
        )
        self._render()
        self._build_toolbar()

    # ---- 툴바 ----
    def _build_toolbar(self) -> None:
        bar = QWidget(self)
        bar.setStyleSheet(
            "QWidget { background: rgba(20,20,20,210); border-radius: 4px; }"
            "QToolButton { color: #eee; border: none; font-size: 13px; }"
            "QToolButton:checked { background: #0078D4; border-radius: 3px; }"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)

        def add_tool(symbol: str, tool: str, tip: str) -> QToolButton:
            b = QToolButton(bar)
            b.setText(symbol)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setFixedSize(26, 22)
            b.clicked.connect(lambda _=False, t=tool: self._set_tool(t))
            self._tool_group.addButton(b)
            lay.addWidget(b)
            return b

        self._btn_move = add_tool("↖", "move", "이동 / 창 옮기기")
        add_tool("□", "rect", "박스")
        add_tool("→", "arrow", "화살표")
        add_tool("✎", "pen", "펜 (자유 그리기)")
        add_tool("T", "text", "글자 넣기")
        self._btn_move.setChecked(True)

        # 색상 (클릭 순환)
        self._btn_color = QToolButton(bar)
        self._btn_color.setText("●")
        self._btn_color.setToolTip("색상 (클릭하면 순환)")
        self._btn_color.setFixedSize(26, 22)
        self._btn_color.clicked.connect(self._cycle_color)
        lay.addWidget(self._btn_color)

        # 굵기 (클릭 순환)
        self._btn_width = QToolButton(bar)
        self._btn_width.setToolTip("선 굵기 (클릭하면 순환)")
        self._btn_width.setFixedSize(24, 22)
        self._btn_width.clicked.connect(self._cycle_width)
        lay.addWidget(self._btn_width)

        # 실행취소
        b_undo = QToolButton(bar)
        b_undo.setText("↺")
        b_undo.setToolTip("실행취소 (Ctrl+Z)")
        b_undo.setFixedSize(24, 22)
        b_undo.clicked.connect(self._undo)
        lay.addWidget(b_undo)

        bar.adjustSize()
        bar.move(0, 0)
        self._toolbar = bar
        self._update_tool_style()

    def _set_tool(self, tool: str) -> None:
        self._commit_text()  # 도구 바꾸면 편집 중 글자 확정
        self._tool = tool
        self.setCursor(Qt.ArrowCursor if tool == "move" else Qt.CrossCursor)

    def _cycle_color(self) -> None:
        self._color_idx = (self._color_idx + 1) % len(PRESET_COLORS)
        self._update_tool_style()

    def _cycle_width(self) -> None:
        try:
            i = PRESET_WIDTHS.index(self._width)
        except ValueError:
            i = 0
        self._width = PRESET_WIDTHS[(i + 1) % len(PRESET_WIDTHS)]
        self._update_tool_style()

    def _update_tool_style(self) -> None:
        col = PRESET_COLORS[self._color_idx]
        self._btn_color.setStyleSheet(f"QToolButton {{ color: {col}; border: none; }}")
        self._btn_width.setText(str(self._width))

    def _undo(self) -> None:
        if self._annotations:
            self._annotations.pop()
            self._render()
            self.changed.emit()

    # ---- 좌표 변환 ----
    def _to_image_point(self, pos: QPointF) -> QPointF:
        """위젯(logical) 좌표 → 이미지 픽셀 좌표."""
        w = max(1, self.width())
        h = max(1, self.height())
        ix = pos.x() * self.pil_image.width / w
        iy = pos.y() * self.pil_image.height / h
        ix = min(max(ix, 0.0), float(self.pil_image.width))
        iy = min(max(iy, 0.0), float(self.pil_image.height))
        return QPointF(ix, iy)

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

        # 주석 그리기 (이미지 픽셀 → 표시 픽셀 배율)
        anns = list(self._annotations)
        if self._draft is not None:
            anns.append(self._draft)
        if anns:
            sx = pix.width() / self.pil_image.width
            sy = pix.height() / self.pil_image.height
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            for ann in anns:
                self._draw_annotation(painter, ann, sx, sy)
            painter.end()

        pix.setDevicePixelRatio(dpr)
        self.setPixmap(pix)
        self.setFixedSize(logical_w, logical_h)
        if getattr(self, "_toolbar", None) is not None:
            self._toolbar.move(0, 0)
            self._toolbar.raise_()

    def _draw_annotation(
        self, painter: QPainter, ann: Annotation, sx: float, sy: float
    ) -> None:
        color = QColor(ann.color)
        wpx = max(1.0, ann.width * sx)
        pen = QPen(color, wpx)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        pts = [(x * sx, y * sy) for (x, y) in ann.points]

        if ann.kind == "rect" and len(pts) >= 2:
            (x0, y0), (x1, y1) = pts[0], pts[1]
            painter.drawRect(
                QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            )
        elif ann.kind == "arrow" and len(pts) >= 2:
            self._draw_arrow(painter, pts[0], pts[1], wpx)
        elif ann.kind == "pen" and len(pts) >= 2:
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for p in pts[1:]:
                path.lineTo(p[0], p[1])
            painter.drawPath(path)
        elif ann.kind == "text" and pts:
            f = QFont()
            f.setPixelSize(max(12, int(ann.width * 6 * sx)))
            painter.setFont(f)
            # points[0] 을 글자 상자의 좌상단으로 취급
            painter.drawText(
                QRectF(pts[0][0], pts[0][1], 100000, 100000),
                Qt.AlignLeft | Qt.AlignTop,
                ann.text,
            )

    @staticmethod
    def _draw_arrow(painter, p0, p1, wpx) -> None:
        x0, y0 = p0
        x1, y1 = p1
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        ang = math.atan2(y1 - y0, x1 - x0)
        L = max(8.0, wpx * 3.5)  # 화살촉 길이
        a = math.radians(26)
        for s in (1, -1):
            hx = x1 - L * math.cos(ang - s * a)
            hy = y1 - L * math.sin(ang - s * a)
            painter.drawLine(QPointF(x1, y1), QPointF(hx, hy))

    # ---- 글자 입력 (인라인) ----
    def _start_text_edit(self, widget_pos: QPointF, image_pt: QPointF) -> None:
        self._commit_text()  # 기존 입력 중이면 먼저 확정
        le = QLineEdit(self)
        le.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,235); color: #000; "
            "border: 1px solid #0078D4; }"
        )
        le.resize(170, 26)
        le.move(int(widget_pos.x()), int(widget_pos.y()))
        le.show()
        le.setFocus()
        self._text_edit = le
        self._text_pt = image_pt
        self._text_committed = False
        le.returnPressed.connect(self._commit_text)
        le.editingFinished.connect(self._commit_text)

    def _commit_text(self) -> None:
        le = self._text_edit
        if le is None or self._text_committed:
            return
        self._text_committed = True
        txt = le.text().strip()
        appended = False
        if txt and self._text_pt is not None:
            self._annotations.append(
                Annotation(
                    "text",
                    PRESET_COLORS[self._color_idx],
                    self._width,
                    [(self._text_pt.x(), self._text_pt.y())],
                    txt,
                )
            )
            appended = True
        le.deleteLater()
        self._text_edit = None
        self._text_pt = None
        self._render()
        if appended:
            self.changed.emit()

    # ---- mouse: draw / drag / wheel / dblclick ----
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._tool == "move":
            self._drag_offset = (
                e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            return
        ipt = self._to_image_point(e.position())
        if self._tool == "text":
            self._start_text_edit(e.position(), ipt)
            return
        col = PRESET_COLORS[self._color_idx]
        if self._tool == "pen":
            self._draft = Annotation("pen", col, self._width, [(ipt.x(), ipt.y())])
        else:  # rect / arrow
            self._draft = Annotation(
                self._tool, col, self._width,
                [(ipt.x(), ipt.y()), (ipt.x(), ipt.y())],
            )

    def mouseMoveEvent(self, e):
        if self._tool == "move":
            if self._drag_offset is not None and (e.buttons() & Qt.LeftButton):
                self.move(e.globalPosition().toPoint() - self._drag_offset)
            return
        if self._draft is None:
            return
        ipt = self._to_image_point(e.position())
        if self._draft.kind == "pen":
            self._draft.points.append((ipt.x(), ipt.y()))
        else:
            self._draft.points[1] = (ipt.x(), ipt.y())
        self._render()

    def mouseReleaseEvent(self, e):
        if self._tool == "move":
            self._drag_offset = None
            return
        if self._draft is None:
            return
        d = self._draft
        self._draft = None
        # 점 찍기 수준의 너무 작은 도형은 버림
        if d.kind in ("rect", "arrow") and len(d.points) >= 2:
            (x0, y0), (x1, y1) = d.points[0], d.points[1]
            if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
                self._render()
                return
        if d.kind == "pen" and len(d.points) < 2:
            self._render()
            return
        self._annotations.append(d)
        self._render()
        self.changed.emit()

    def mouseDoubleClickEvent(self, _e):
        if self._tool == "move":
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
        elif e.key() == Qt.Key_Z and e.modifiers() & Qt.ControlModifier:
            self._undo()
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
        a_undo = menu.addAction("주석 실행취소  (Ctrl+Z)")
        a_clear = menu.addAction("주석 전체 지우기")
        menu.addSeparator()
        a_reset = menu.addAction("원본 크기  (0)")
        a_close = menu.addAction("닫기  (Esc)")
        chosen = menu.exec(e.globalPos())
        if chosen == a_copy:
            self.copy_to_clipboard()
        elif chosen == a_save:
            self.save_as()
        elif chosen == a_undo:
            self._undo()
        elif chosen == a_clear:
            self._annotations.clear()
            self._render()
            self.changed.emit()
        elif chosen == a_reset:
            self._scale = 1.0
            self._render()
        elif chosen == a_close:
            self.close()

    # ---- actions ----
    def _flatten(self) -> Image.Image:
        """주석을 이미지에 구워 넣은 최종 PIL 이미지. 주석 없으면 원본 그대로."""
        self._commit_text()
        if not self._annotations:
            return self.pil_image
        qimg = pil_to_qimage(self.pil_image)
        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        for ann in self._annotations:
            self._draw_annotation(painter, ann, 1.0, 1.0)
        painter.end()
        return qimage_to_pil(qimg)

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setImage(pil_to_clipboard_image(self._flatten()))
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
            save_pil_with_dpi(self._flatten(), path)
