"""Optional raw/simulated I/Q constellation panel and renderer."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from gui import ComplexArray, PanelContext, normalize_appearance_mode


class ConstellationWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: ComplexArray | None = None
        self._appearance_mode = "green"
        self.setMinimumSize(240, 210)

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = normalize_appearance_mode(appearance_mode)
        self.update()

    def _colors(self) -> tuple[QColor, QColor, QColor, QColor]:
        if self._appearance_mode == "normal":
            return (
                QColor("#ffffff"),
                QColor("#c4ced8"),
                QColor("#405466"),
                QColor(22, 119, 74, 155),
            )
        if self._appearance_mode == "green":
            return (
                QColor("#0d1712"),
                QColor("#294c37"),
                QColor("#a8c6b2"),
                QColor(113, 220, 153, 145),
            )
        return (
            QColor("#111925"),
            QColor("#29384b"),
            QColor("#91a3b8"),
            QColor(129, 230, 168, 130),
        )

    def set_points(self, points: ComplexArray) -> None:
        self._points = points
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel_color, grid_color, text_color, point_color = self._colors()
        painter.fillRect(self.rect(), panel_color)
        side = max(10.0, min(self.width(), self.height()) - 38.0)
        plot = QRectF((self.width() - side) / 2, 12, side, side)
        center = plot.center()
        painter.setPen(QPen(grid_color, 1))
        painter.drawEllipse(plot)
        painter.drawLine(
            QPointF(plot.left(), center.y()), QPointF(plot.right(), center.y())
        )
        painter.drawLine(
            QPointF(center.x(), plot.top()), QPointF(center.x(), plot.bottom())
        )
        painter.drawEllipse(center, side * 0.25, side * 0.25)
        painter.setPen(text_color)
        painter.drawText(QPointF(plot.right() - 8, center.y() - 6), "I")
        painter.drawText(QPointF(center.x() + 7, plot.top() + 12), "Q")

        if self._points is None or self._points.size == 0:
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for symbols"
            )
            return
        painter.setPen(QPen(point_color, 2.4))
        scale = side * 0.42
        for point in self._points:
            x = center.x() + float(point.real) * scale
            y = center.y() - float(point.imag) * scale
            if plot.contains(QPointF(x, y)):
                painter.drawPoint(QPointF(x, y))


class Panel(ConstellationWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        context.bus.receiver_snapshot.connect(self._show_snapshot)
        context.bus.appearance_changed.connect(self.set_appearance)
        self.set_appearance(context.state.appearance_mode)
        if context.state.receiver_snapshot is not None:
            self._show_snapshot(context.state.receiver_snapshot)

    def _show_snapshot(self, snapshot) -> None:
        self.set_points(snapshot.constellation)
