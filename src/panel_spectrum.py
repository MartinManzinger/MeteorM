"""Optional spectrum panel with spectral and bounded waterfall renderers."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from gui import PanelContext, ReceiverStatus, SpectrumFrame, normalize_appearance_mode


PLOT_LEFT = 54.0
PLOT_RIGHT = 16.0


def frequency_ticks(
    first_frequency_hz: float,
    last_frequency_hz: float,
    *,
    target_count: int = 5,
) -> tuple[float, ...]:
    """Return round 1/2/5-decade ticks within a frequency span."""
    span = last_frequency_hz - first_frequency_hz
    if span <= 0 or target_count < 2:
        return ()
    raw_step = span / (target_count - 1)
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1.0:
        multiplier = 1.0
    elif normalized <= 2.0:
        multiplier = 2.0
    elif normalized <= 5.0:
        multiplier = 5.0
    else:
        multiplier = 10.0
    step = multiplier * magnitude
    first_tick = math.ceil(first_frequency_hz / step) * step
    count = int(math.floor((last_frequency_hz - first_tick) / step)) + 1
    return tuple(first_tick + index * step for index in range(max(0, count)))


def format_frequency_tick(frequency_hz: float, step_hz: float) -> str:
    step_mhz = step_hz / 1e6
    decimals = max(0, min(6, math.ceil(-math.log10(step_mhz))))
    return f"{frequency_hz / 1e6:.{decimals}f}"


def plot_rect(width: int, height: int, *, top: float, bottom: float) -> QRectF:
    return QRectF(
        PLOT_LEFT,
        top,
        max(10.0, width - PLOT_LEFT - PLOT_RIGHT),
        max(10.0, height - top - bottom),
    )


def x_for_frequency(
    frequency_hz: float,
    first_frequency_hz: float,
    last_frequency_hz: float,
    plot: QRectF,
) -> float:
    fraction = (frequency_hz - first_frequency_hz) / (
        last_frequency_hz - first_frequency_hz
    )
    return plot.left() + fraction * plot.width()


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
    if appearance_mode == "green":
        return ChartTheme(
            QColor("#0d1712"),
            QColor("#294c37"),
            QColor("#a8c6b2"),
            QColor("#71dc99"),
        )
    return ChartTheme(
        QColor("#111925"),
        QColor("#29384b"),
        QColor("#91a3b8"),
        QColor("#55d7ff"),
    )


class SpectrumWidget(QWidget):
    frequency_selected = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: SpectrumFrame | None = None
        self._appearance_mode = "green"
        self._theme = chart_theme(self._appearance_mode)
        self.setMinimumSize(360, 210)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("Left-click the spectrum to tune the receiver")

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = normalize_appearance_mode(appearance_mode)
        self._theme = chart_theme(self._appearance_mode)
        self.update()

    def set_frame(self, frame: SpectrumFrame) -> None:
        self._frame = frame
        self.update()

    def plot_rect(self) -> QRectF:
        return plot_rect(self.width(), self.height(), top=18.0, bottom=10.0)

    def frequency_at(self, x: float) -> float | None:
        if self._frame is None or self._frame.frequency_hz.size < 2:
            return None
        plot = self.plot_rect()
        if x < plot.left() or x > plot.right():
            return None
        fraction = (x - plot.left()) / plot.width()
        first = float(self._frame.frequency_hz[0])
        last = float(self._frame.frequency_hz[-1])
        return first + fraction * (last - first)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.plot_rect().contains(event.position())
        ):
            frequency_hz = self.frequency_at(event.position().x())
            if frequency_hz is not None:
                self.frequency_selected.emit(frequency_hz)
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._theme.surface)
        plot = self.plot_rect()
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
        for x in self._vertical_grid_positions(plot):
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(QPen(self._theme.text, 1))
        painter.drawRect(plot)

    def _vertical_grid_positions(self, plot: QRectF) -> tuple[float, ...]:
        if self._frame is None or self._frame.frequency_hz.size < 2:
            return tuple(plot.left() + plot.width() * index / 4 for index in range(5))
        first = float(self._frame.frequency_hz[0])
        last = float(self._frame.frequency_hz[-1])
        return tuple(
            x_for_frequency(tick, first, last, plot)
            for tick in frequency_ticks(first, last)
        )


class WaterfallWidget(QWidget):
    frequency_selected = Signal(float)

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
        self._appearance_mode = "green"
        self._theme = chart_theme(self._appearance_mode)
        self._colors = self._make_color_table(self._appearance_mode)
        self.setMinimumSize(360, 160)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("Left-click the waterfall to tune the receiver")

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = normalize_appearance_mode(appearance_mode)
        self._theme = chart_theme(self._appearance_mode)
        self._colors = self._make_color_table(self._appearance_mode)
        self.update()

    def set_frame(self, frame: SpectrumFrame) -> None:
        if frame.power_db.size == 0:
            return
        first_frequency_hz = float(frame.frequency_hz[0])
        last_frequency_hz = float(frame.frequency_hz[-1])
        if (
            self._first_frequency_hz is not None
            and self._last_frequency_hz is not None
            and (
                first_frequency_hz != self._first_frequency_hz
                or last_frequency_hz != self._last_frequency_hz
            )
        ):
            self._rows.clear()
        source_indices = np.arange(frame.power_db.size, dtype=np.float64)
        target_indices = np.linspace(
            0.0, frame.power_db.size - 1, self._frequency_bins
        )
        power = np.interp(target_indices, source_indices, frame.power_db)
        normalized = np.clip((power + 115.0) / 95.0 * 255.0, 0.0, 255.0)
        self._rows.append(np.asarray(normalized, dtype=np.uint8))
        self._first_frequency_hz = first_frequency_hz
        self._last_frequency_hz = last_frequency_hz
        self.update()

    def plot_rect(self) -> QRectF:
        return plot_rect(self.width(), self.height(), top=8.0, bottom=30.0)

    def frequency_at(self, x: float) -> float | None:
        if self._first_frequency_hz is None or self._last_frequency_hz is None:
            return None
        plot = self.plot_rect()
        if x < plot.left() or x > plot.right():
            return None
        fraction = (x - plot.left()) / plot.width()
        return self._first_frequency_hz + fraction * (
            self._last_frequency_hz - self._first_frequency_hz
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.plot_rect().contains(event.position())
        ):
            frequency_hz = self.frequency_at(event.position().x())
            if frequency_hz is not None:
                self.frequency_selected.emit(frequency_hz)
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._theme.surface)
        plot = self.plot_rect()
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
            self._draw_frequency_axis(painter, plot)

    def _draw_frequency_axis(self, painter: QPainter, plot: QRectF) -> None:
        first = self._first_frequency_hz
        last = self._last_frequency_hz
        if first is None or last is None:
            return
        ticks = frequency_ticks(first, last)
        if not ticks:
            return
        step_hz = ticks[1] - ticks[0] if len(ticks) > 1 else last - first
        painter.setPen(QPen(self._theme.grid, 1))
        for tick in ticks:
            x = x_for_frequency(tick, first, last, plot)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
            painter.setPen(self._theme.text)
            painter.drawText(
                QRectF(x - 40, plot.bottom() + 5, 80, 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                format_frequency_tick(tick, step_hz),
            )
            painter.setPen(QPen(self._theme.grid, 1))
        painter.setPen(self._theme.text)
        painter.drawText(
            QRectF(4, plot.bottom() + 5, PLOT_LEFT - 10, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            "MHz",
        )

    @staticmethod
    def _make_color_table(appearance_mode: str) -> np.ndarray:
        if appearance_mode == "normal":
            anchors = (
                (0.00, (245, 248, 252)),
                (0.22, (198, 218, 236)),
                (0.45, (54, 137, 166)),
                (0.68, (39, 145, 107)),
                (0.84, (225, 147, 45)),
                (1.00, (151, 42, 57)),
            )
        elif appearance_mode == "green":
            anchors = (
                (0.00, (3, 12, 7)),
                (0.22, (10, 42, 24)),
                (0.45, (22, 105, 59)),
                (0.68, (83, 194, 118)),
                (0.84, (219, 183, 77)),
                (1.00, (255, 248, 220)),
            )
        else:
            anchors = (
                (0.00, (4, 9, 18)),
                (0.22, (18, 41, 79)),
                (0.45, (24, 126, 139)),
                (0.68, (129, 230, 168)),
                (0.84, (255, 180, 84)),
                (1.00, (255, 247, 232)),
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
        self.context = context
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.source_status = QLabel()
        layout.addWidget(self.source_status)
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
        self.spectrum.frequency_selected.connect(self._request_tune)
        self.waterfall.frequency_selected.connect(self._request_tune)
        context.bus.receiver_snapshot.connect(self._show_snapshot)
        context.bus.receiver_status_changed.connect(self._show_status)
        context.bus.appearance_changed.connect(self._show_appearance)
        self._show_appearance(context.state.appearance_mode)
        self._show_status(context.state.receiver_status)
        if context.state.receiver_snapshot is not None:
            self._show_snapshot(context.state.receiver_snapshot)

    def _show_snapshot(self, snapshot) -> None:
        self.spectrum.set_frame(snapshot.spectrum)
        self.waterfall.set_frame(snapshot.spectrum)

    def _show_status(self, status: ReceiverStatus) -> None:
        self.source_status.setText(
            f"{status.backend} · {status.state} · {status.message}"
        )

    def _request_tune(self, frequency_hz: float) -> None:
        settings = replace(
            self.context.state.receiver_settings,
            center_frequency_hz=float(round(frequency_hz)),
        )
        self.context.bus.receiver_settings_requested.emit(settings)

    def _show_appearance(self, appearance_mode: str) -> None:
        self.spectrum.set_appearance(appearance_mode)
        self.waterfall.set_appearance(appearance_mode)
