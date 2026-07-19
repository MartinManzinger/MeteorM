import time

import pytest

from gui import ReceiverSettings
from main import SimulationBackend


def test_simulator_publishes_latest_snapshot_and_stops() -> None:
    backend = SimulationBackend(update_rate_hz=30, queue_size=1)
    settings = ReceiverSettings(center_frequency_hz=137_100_000.0)
    backend.update_settings(settings)
    backend.start()
    deadline = time.monotonic() + 2.0
    snapshot = None
    while snapshot is None and time.monotonic() < deadline:
        time.sleep(0.02)
        snapshot = backend.take_latest()
    backend.stop()

    assert snapshot is not None
    assert not backend.running
    bin_width = settings.sample_rate_hz / snapshot.spectrum.frequency_hz.size
    assert snapshot.spectrum.frequency_hz.mean() == pytest.approx(
        settings.center_frequency_hz, abs=bin_width
    )
    assert snapshot.constellation.size <= 1200
