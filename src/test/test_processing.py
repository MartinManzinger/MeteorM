import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from main import ConstellationProcessor, SpectrumProcessor
from panel_spectrum import WaterfallWidget


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
