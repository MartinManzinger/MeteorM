"""Optional spectrum panel with spectral and bounded waterfall renderers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from gui import PanelContext, SpectrumFrame


@dataclass(frozen=True, slots=True)
class ChartTheme:
    surface: QColor
    grid: QColor
    text: QColor
    accent: QColor


def chart_theme(appearance_mode: str) -> ChartTheme:
    if appearance_mode == "normal":
        return ChartTheme(
            QColor("#ffffff"),
            QColor("#c4ced8"),
            QColor("#405466"),
            QColor("#007c9f"),
        )
    return ChartTheme(
        QColor("#111925"),
        QColor("#29384b"),
        QColor("#91a3b8"),
        QColor("#55d7ff"),
    )


class SpectrumWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: SpectrumFrame | None = None
        self._appearance_mode = "dark"
        self._theme = chart_theme(self._appearance_mode)
        self.setMinimumSize(360, 210)

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = "normal" if appearance_mode == "normal" else "dark"
        self._theme = chart_theme(self._appearance_mode)
        self.update()

    def set_frame(self, frame: SpectrumFrame) -> None:
        self._frame = frame
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._theme.surface)
        plot = QRectF(54, 18, max(10, self.width() - 70), max(10, self.height() - 48))
        self._draw_grid(painter, plot)
        if self._frame is None or self._frame.power_db.size < 2:
            painter.setPen(self._theme.text)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for samples"
            )
            return

        power = self._frame.power_db
        xs = np.linspace(plot.left(), plot.right(), power.size)
        ys = plot.bottom() - np.clip(power / 120.0 + 1.0, 0.0, 1.0) * plot.height()
        polygon = QPolygonF(
            [QPointF(float(x), float(y)) for x, y in zip(xs, ys, strict=True)]
        )
        painter.setPen(QPen(self._theme.accent, 1.4))
        painter.drawPolyline(polygon)
        frequencies_mhz = self._frame.frequency_hz / 1e6
        painter.setPen(self._theme.text)
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 7, plot.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            f"{frequencies_mhz[0]:.3f} — {frequencies_mhz[-1]:.3f} MHz",
        )

    def _draw_grid(self, painter: QPainter, plot: QRectF) -> None:
        painter.setPen(QPen(self._theme.grid, 1))
        for index in range(7):
            y = plot.top() + plot.height() * index / 6
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(self._theme.text)
            painter.drawText(
                QRectF(4, y - 9, 43, 18),
                Qt.AlignmentFlag.AlignRight,
                f"{-index * 20}",
            )
            painter.setPen(QPen(self._theme.grid, 1))
        for index in range(9):
            x = plot.left() + plot.width() * index / 8
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(QPen(self._theme.text, 1))
        painter.drawRect(plot)


class WaterfallWidget(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        history_rows: int = 180,
        frequency_bins: int = 512,
    ) -> None:
        super().__init__(parent)
        self._rows: deque[np.ndarray] = deque(maxlen=history_rows)
        self._frequency_bins = frequency_bins
        self._first_frequency_hz: float | None = None
        self._last_frequency_hz: float | None = None
        self._appearance_mode = "dark"
        self._theme = chart_theme(self._appearance_mode)
        self._colors = self._make_color_table(self._appearance_mode)
        self.setMinimumSize(360, 160)

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = "normal" if appearance_mode == "normal" else "dark"
        self._theme = chart_theme(self._appearance_mode)
        self._colors = self._make_color_table(self._appearance_mode)
        self.update()

    def set_frame(self, frame: SpectrumFrame) -> None:
        if frame.power_db.size == 0:
            return
        source_indices = np.arange(frame.power_db.size, dtype=np.float64)
        target_indices = np.linspace(
            0.0, frame.power_db.size - 1, self._frequency_bins
        )
        power = np.interp(target_indices, source_indices, frame.power_db)
        normalized = np.clip((power + 115.0) / 95.0 * 255.0, 0.0, 255.0)
        self._rows.append(np.asarray(normalized, dtype=np.uint8))
        self._first_frequency_hz = float(frame.frequency_hz[0])
        self._last_frequency_hz = float(frame.frequency_hz[-1])
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._theme.surface)
        plot = QRectF(8, 8, max(10, self.width() - 16), max(10, self.height() - 34))
        if not self._rows:
            painter.setPen(self._theme.text)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for history"
            )
            return
        intensities = np.vstack(tuple(reversed(self._rows)))
        pixels = np.ascontiguousarray(self._colors[intensities])
        image = QImage(
            pixels.data,
            pixels.shape[1],
            pixels.shape[0],
            pixels.strides[0],
            QImage.Format.Format_ARGB32,
        )
        painter.drawImage(plot, image)
        painter.setPen(QPen(self._theme.text, 1))
        painter.drawRect(plot)
        if self._first_frequency_hz is not None and self._last_frequency_hz is not None:
            painter.drawText(
                QRectF(plot.left(), plot.bottom() + 5, plot.width(), 18),
                Qt.AlignmentFlag.AlignCenter,
                f"newest at top · {self._first_frequency_hz / 1e6:.3f} — "
                f"{self._last_frequency_hz / 1e6:.3f} MHz",
            )

    @staticmethod
    def _make_color_table(appearance_mode: str) -> np.ndarray:
        anchors = (
            (
                (0.00, (245, 248, 252)),
                (0.22, (198, 218, 236)),
                (0.45, (54, 137, 166)),
                (0.68, (39, 145, 107)),
                (0.84, (225, 147, 45)),
                (1.00, (151, 42, 57)),
            )
            if appearance_mode == "normal"
            else (
                (0.00, (4, 9, 18)),
                (0.22, (18, 41, 79)),
                (0.45, (24, 126, 139)),
                (0.68, (129, 230, 168)),
                (0.84, (255, 180, 84)),
                (1.00, (255, 247, 232)),
            )
        )
        table = np.empty(256, dtype=np.uint32)
        for index in range(256):
            position = index / 255.0
            for anchor_index in range(len(anchors) - 1):
                left_position, left_color = anchors[anchor_index]
                right_position, right_color = anchors[anchor_index + 1]
                if position <= right_position:
                    amount = (position - left_position) / (
                        right_position - left_position
                    )
                    red, green, blue = (
                        round(left + (right - left) * amount)
                        for left, right in zip(left_color, right_color, strict=True)
                    )
                    table[index] = QColor(red, green, blue).rgba()
                    break
        return table


class Panel(QWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        splitter = QSplitter(Qt.Orientation.Vertical)

        spectrum_section = QWidget()
        spectrum_layout = QVBoxLayout(spectrum_section)
        spectrum_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_layout.addWidget(QLabel("SPECTRAL POWER"))
        self.spectrum = SpectrumWidget()
        spectrum_layout.addWidget(self.spectrum, 1)

        waterfall_section = QWidget()
        waterfall_layout = QVBoxLayout(waterfall_section)
        waterfall_layout.setContentsMargins(0, 0, 0, 0)
        waterfall_layout.addWidget(QLabel("WATERFALL HISTORY"))
        self.waterfall = WaterfallWidget()
        waterfall_layout.addWidget(self.waterfall, 1)

        splitter.addWidget(spectrum_section)
        splitter.addWidget(waterfall_section)
        splitter.setSizes((260, 220))
        layout.addWidget(splitter)
        context.bus.receiver_snapshot.connect(self._show_snapshot)
        context.bus.appearance_changed.connect(self._show_appearance)
        self._show_appearance(context.state.appearance_mode)
        if context.state.receiver_snapshot is not None:
            self._show_snapshot(context.state.receiver_snapshot)

    def _show_snapshot(self, snapshot) -> None:
        self.spectrum.set_frame(snapshot.spectrum)
        self.waterfall.set_frame(snapshot.spectrum)

    def _show_appearance(self, appearance_mode: str) -> None:
        self.spectrum.set_appearance(appearance_mode)
        self.waterfall.set_appearance(appearance_mode)
