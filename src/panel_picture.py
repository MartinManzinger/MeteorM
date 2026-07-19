"""Optional decoded-picture panel and LRPT image renderer."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from gui import ImageArray, PanelContext


class LRPTImageWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: ImageArray | None = None
        self._visible_lines = 0
        self._appearance_mode = "dark"
        self.setMinimumSize(400, 225)

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = "normal" if appearance_mode == "normal" else "dark"
        self.update()

    def set_image(self, image: ImageArray | None, visible_lines: int) -> None:
        if image is not None:
            self._image = image
        self._visible_lines = visible_lines
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        normal = self._appearance_mode == "normal"
        painter.fillRect(
            self.rect(), QColor("#ffffff") if normal else QColor("#0d141d")
        )
        if self._image is None:
            painter.setPen(QColor("#405466") if normal else QColor("#91a3b8"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for image data"
            )
            return

        height, width, _ = self._image.shape
        image = QImage(
            self._image.data,
            width,
            height,
            int(self._image.strides[0]),
            QImage.Format.Format_RGB888,
        )
        target = QRectF(self.rect()).adjusted(8, 8, -8, -28)
        source_ratio = width / height
        if target.width() / target.height() > source_ratio:
            draw_width = target.height() * source_ratio
            target.setLeft(target.center().x() - draw_width / 2)
            target.setWidth(draw_width)
        else:
            draw_height = target.width() / source_ratio
            target.setTop(target.center().y() - draw_height / 2)
            target.setHeight(draw_height)
        painter.drawImage(target, image)
        painter.setPen(QColor("#405466") if normal else QColor("#a8bacb"))
        painter.drawText(
            QRectF(8, self.height() - 23, self.width() - 16, 18),
            Qt.AlignmentFlag.AlignCenter,
            f"Simulated LRPT composite · {self._visible_lines}/{height} lines",
        )


class Panel(LRPTImageWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        context.bus.receiver_snapshot.connect(self._show_snapshot)
        context.bus.appearance_changed.connect(self.set_appearance)
        self.set_appearance(context.state.appearance_mode)
        if context.state.receiver_snapshot is not None:
            self._show_snapshot(context.state.receiver_snapshot)

    def _show_snapshot(self, snapshot) -> None:
        self.set_image(snapshot.image_rgb, snapshot.image_lines)
