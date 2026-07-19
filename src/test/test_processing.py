import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from gui import (
    AppEventBus,
    ApplicationState,
    PanelContext,
    ReceiverLocation,
    ReceiverSettings,
)
from main import ConstellationProcessor, SpectrumProcessor
from panel_spectrum import Panel, SpectrumWidget, WaterfallWidget, frequency_ticks


def test_spectrum_processor_finds_tone_near_expected_frequency() -> None:
    sample_rate = 2_048_000.0
    center = 137_900_000.0
    offset = 128_000.0
    count = 2048
    time = np.arange(count) / sample_rate
    iq = np.exp(2j * np.pi * offset * time).astype(np.complex64)

    frame = SpectrumProcessor(fft_size=count).prepare(iq, sample_rate, center)

    peak_frequency = frame.frequency_hz[np.argmax(frame.power_db)]
    assert peak_frequency == pytest.approx(center + offset, abs=sample_rate / count)
    assert not frame.frequency_hz.flags.writeable
    assert not frame.power_db.flags.writeable


def test_spectrum_processor_rejects_short_input() -> None:
    with pytest.raises(ValueError, match="IQ samples"):
        SpectrumProcessor(fft_size=64).prepare(
            np.ones(32, dtype=np.complex64), 2_000_000.0, 137_900_000.0
        )


def test_constellation_processor_bounds_and_freezes_points() -> None:
    iq = np.arange(5000, dtype=np.float32).astype(np.complex64) * (1 + 1j)

    points = ConstellationProcessor(max_points=250).prepare(iq)

    assert points.size <= 250
    assert points.dtype == np.complex64
    assert not points.flags.writeable
    assert np.percentile(np.abs(points), 95) == pytest.approx(1.0, rel=0.01)


def test_waterfall_keeps_a_bounded_resampled_history() -> None:
    app = QApplication.instance() or QApplication([])
    widget = WaterfallWidget(history_rows=5, frequency_bins=32)
    processor = SpectrumProcessor(fft_size=64)
    iq = np.ones(64, dtype=np.complex64)
    frame = processor.prepare(iq, 2_000_000.0, 137_900_000.0)

    for _ in range(12):
        widget.set_frame(frame)

    assert len(widget._rows) == 5
    assert all(row.shape == (32,) for row in widget._rows)
    widget.resize(480, 220)
    widget.show()
    app.processEvents()
    assert not widget.grab().isNull()
    widget.close()
    app.processEvents()


def test_spectrum_and_waterfall_share_horizontal_plot_geometry() -> None:
    spectrum = SpectrumWidget()
    waterfall = WaterfallWidget()
    spectrum.resize(640, 240)
    waterfall.resize(640, 240)

    assert spectrum.plot_rect().left() == waterfall.plot_rect().left()
    assert spectrum.plot_rect().right() == waterfall.plot_rect().right()
    assert frequency_ticks(136_900_000.0, 138_900_000.0) == (
        137_000_000.0,
        137_500_000.0,
        138_000_000.0,
        138_500_000.0,
    )


def test_waterfall_clears_history_when_frequency_span_changes() -> None:
    widget = WaterfallWidget(history_rows=5, frequency_bins=32)
    processor = SpectrumProcessor(fft_size=64)
    iq = np.ones(64, dtype=np.complex64)
    initial = processor.prepare(iq, 2_000_000.0, 137_900_000.0)
    retuned = processor.prepare(iq, 2_000_000.0, 138_400_000.0)

    for _ in range(4):
        widget.set_frame(initial)
    widget.set_frame(retuned)

    assert len(widget._rows) == 1


def test_clicking_spectrum_or_waterfall_requests_clicked_frequency() -> None:
    app = QApplication.instance() or QApplication([])
    bus = AppEventBus()
    state = ApplicationState(ReceiverSettings(), ReceiverLocation())
    panel = Panel(PanelContext(bus, state))
    panel.resize(720, 640)
    panel.show()
    app.processEvents()
    processor = SpectrumProcessor()
    frame = processor.prepare(
        np.ones(2048, dtype=np.complex64), 2_000_000.0, 137_900_000.0
    )
    panel.spectrum.set_frame(frame)
    panel.waterfall.set_frame(frame)
    requested = []
    bus.receiver_settings_requested.connect(requested.append)

    spectrum_plot = panel.spectrum.plot_rect()
    spectrum_position = QPointF(
        spectrum_plot.left() + 0.75 * spectrum_plot.width(),
        spectrum_plot.center().y(),
    )
    panel.spectrum.mousePressEvent(LeftClick(spectrum_position))
    expected_spectrum = panel.spectrum.frequency_at(spectrum_position.x())
    assert requested[-1].center_frequency_hz == pytest.approx(
        expected_spectrum, abs=1.0
    )

    waterfall_plot = panel.waterfall.plot_rect()
    waterfall_position = QPointF(
        waterfall_plot.left() + 0.25 * waterfall_plot.width(),
        waterfall_plot.center().y(),
    )
    panel.waterfall.mousePressEvent(LeftClick(waterfall_position))
    expected_waterfall = panel.waterfall.frequency_at(waterfall_position.x())
    assert requested[-1].center_frequency_hz == pytest.approx(
        expected_waterfall, abs=1.0
    )
    panel.close()
    app.processEvents()


class LeftClick:
    def __init__(self, position: QPointF) -> None:
        self._position = position
        self.accepted = False

    @staticmethod
    def button():
        return Qt.MouseButton.LeftButton

    def position(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True
