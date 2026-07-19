from __future__ import annotations

import subprocess
import time

import numpy as np

from gui import HACKRF_AUTO_DEVICE, ReceiverSettings
from main import (
    GNUradioBridge,
    GNUradioProcessBridge,
    HackRFController,
    ReceiverError,
)


class FakeSoapySource:
    def __init__(self) -> None:
        self.calls = []

    def set_sample_rate(self, channel, value) -> None:
        self.calls.append(("sample_rate", channel, value))

    def set_bandwidth(self, channel, value) -> None:
        self.calls.append(("bandwidth", channel, value))

    def set_frequency(self, channel, value) -> None:
        self.calls.append(("frequency", channel, value))

    def set_gain(self, channel, name, value) -> None:
        self.calls.append(("gain", channel, name, value))


class FakeBridge:
    def __init__(self, settings: ReceiverSettings) -> None:
        self.settings = settings
        self.samples_received = 4096
        self.dropped_buffers = 0
        self.started = False
        self.stopped = False
        self._block = np.exp(
            2j * np.pi * np.arange(4096, dtype=np.float32) / 64.0
        ).astype(np.complex64)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update_settings(self, settings: ReceiverSettings) -> None:
        self.settings = settings

    def take_latest(self):
        block, self._block = self._block, None
        return block


def test_gnuradio_bridge_maps_receive_controls_to_soapy_source() -> None:
    source = FakeSoapySource()
    settings = ReceiverSettings(
        center_frequency_hz=137_900_000.0,
        sample_rate_hz=3_000_000.0,
        filter_bandwidth_hz=1_750_000.0,
        lna_gain_db=32,
        vga_gain_db=30,
        amplifier_enabled=True,
        device="HackRF One [00000001abcdef]",
    )

    GNUradioBridge._configure_source(source, settings)

    assert source.calls == [
        ("sample_rate", 0, 3_000_000.0),
        ("bandwidth", 0, 1_750_000.0),
        ("frequency", 0, 137_900_000.0),
        ("gain", 0, "AMP", True),
        ("gain", 0, "LNA", 32),
        ("gain", 0, "VGA", 30),
    ]
    assert GNUradioBridge._device_args(settings.device) == "serial=00000001abcdef"
    assert GNUradioBridge._device_args(HACKRF_AUTO_DEVICE) == ""


def test_gnuradio_bridge_assembles_short_scheduler_buffers(monkeypatch) -> None:
    monkeypatch.setattr(GNUradioBridge, "_build", lambda self, settings: None)
    bridge = GNUradioBridge(ReceiverSettings(), block_size=4096)
    source = np.arange(4096, dtype=np.float32).astype(np.complex64)

    for part in np.array_split(source, 4):
        bridge._accept_samples(part)

    block = bridge.take_latest()
    assert block is not None
    assert np.array_equal(block, source)
    assert not block.flags.writeable
    assert bridge.samples_received == 4096


def test_gnuradio_bridge_rejects_unsafe_or_fractional_sample_rates() -> None:
    for sample_rate_hz in (2_500_000.0, 21_000_000.0):
        try:
            GNUradioBridge._validate_sample_rate(sample_rate_hz)
        except ReceiverError as error:
            assert "Sample rate" in str(error)
        else:
            raise AssertionError("unsupported sample rate was accepted")
    GNUradioBridge._validate_sample_rate(20_000_000.0)
    assert GNUradioBridge.analysis_decimation(20_000_000.0, 4096, 12.0) == 407


def test_hackrf_discovery_parses_each_serial(monkeypatch) -> None:
    output = """Found HackRF
Index: 0
Serial number: 00000001abcdef
Found HackRF
Index: 1
Serial number: 00000002fedcba
"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, output, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert HackRFController.discover_devices() == (
        "HackRF One [00000001abcdef]",
        "HackRF One [00000002fedcba]",
    )


def test_hackrf_discovery_reports_driver_failure(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, "", "hackrf_init() failed: Other error (-1000)"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        HackRFController.discover_devices()
    except ReceiverError as error:
        assert "hackrf_init() failed" in str(error)
    else:
        raise AssertionError("discovery failure was not reported")


def test_hackrf_controller_converts_bridge_iq_to_snapshot() -> None:
    created = []

    def make_bridge(settings):
        bridge = FakeBridge(settings)
        created.append(bridge)
        return bridge

    controller = HackRFController(
        bridge_factory=make_bridge,
        device_probe=lambda: ("HackRF One [00000001abcdef]",),
    )
    settings = ReceiverSettings(device=HACKRF_AUTO_DEVICE)
    controller.update_settings(settings)
    controller.start()
    deadline = time.monotonic() + 2.0
    snapshot = None
    while snapshot is None and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = controller.take_latest()
    controller.stop()

    assert snapshot is not None
    assert created[0].started
    assert created[0].stopped
    assert snapshot.spectrum.frequency_hz.size == 2048
    assert snapshot.constellation.size <= 1200
    assert snapshot.image_rgb is None
    assert not snapshot.symbol_sync
    assert not snapshot.frame_sync


def test_sample_rate_change_restarts_the_flowgraph() -> None:
    created = []

    def make_bridge(settings):
        bridge = FakeBridge(settings)
        created.append(bridge)
        return bridge

    controller = HackRFController(
        bridge_factory=make_bridge,
        device_probe=lambda: ("HackRF One [00000001abcdef]",),
        sample_timeout=0.5,
    )
    controller.update_settings(ReceiverSettings(device=HACKRF_AUTO_DEVICE))
    controller.start()
    deadline = time.monotonic() + 1.0
    while len(created) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    controller.update_settings(
        ReceiverSettings(device=HACKRF_AUTO_DEVICE, sample_rate_hz=3_000_000.0)
    )
    deadline = time.monotonic() + 1.0
    while len(created) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    controller.stop()

    assert len(created) == 2
    assert created[0].stopped
    assert created[1].started
    assert created[1].settings.sample_rate_hz == 3_000_000.0


def test_controller_can_restart_after_sample_timeout() -> None:
    created = []

    def make_bridge(settings):
        bridge = FakeBridge(settings)
        if not created:
            bridge._block = None
        created.append(bridge)
        return bridge

    controller = HackRFController(
        bridge_factory=make_bridge,
        device_probe=lambda: ("HackRF One [00000001abcdef]",),
        sample_timeout=0.05,
    )
    controller.update_settings(ReceiverSettings(device=HACKRF_AUTO_DEVICE))
    controller.start()
    deadline = time.monotonic() + 1.0
    while controller.running and time.monotonic() < deadline:
        time.sleep(0.01)

    assert controller.status.state == "error"
    assert "No IQ samples" in controller.status.message
    assert created[0].stopped

    controller.start()
    deadline = time.monotonic() + 1.0
    snapshot = None
    while snapshot is None and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = controller.take_latest()
    controller.stop()

    assert snapshot is not None
    assert len(created) == 2
    assert created[1].started
    assert created[1].stopped


def test_process_bridge_terminates_an_unresponsive_native_worker() -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True
            self.terminated = False

        def join(self, timeout) -> None:
            del timeout

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def kill(self) -> None:
            self.alive = False

    class FakeEvent:
        def __init__(self) -> None:
            self.was_set = False

        def set(self) -> None:
            self.was_set = True

    class FakeQueue:
        def close(self) -> None:
            pass

        def cancel_join_thread(self) -> None:
            pass

    bridge = GNUradioProcessBridge(ReceiverSettings())
    process = FakeProcess()
    stop_event = FakeEvent()
    bridge._process = process
    bridge._stop_event = stop_event
    bridge._iq_queue = FakeQueue()
    bridge._command_queue = FakeQueue()
    bridge._event_queue = FakeQueue()

    bridge.stop(timeout=0.01)

    assert stop_event.was_set
    assert process.terminated
    assert bridge._process is None
