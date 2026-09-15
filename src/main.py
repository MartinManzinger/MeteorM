
# Copyright (C) 2026 Martin Manzinger
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
 
"""Application services, backend orchestration, and executable entry point."""

from __future__ import annotations

import copy
import logging
import math
import multiprocessing
import queue
import re
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import yaml
from numpy.typing import NDArray
from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QApplication
from skyfield.api import EarthSatellite, load, wgs84

from gui import (
    APPEARANCE_MODES,
    APPLICATION_DESKTOP_ID,
    APPLICATION_NAME,
    HACKRF_AUTO_DEVICE,
    SIMULATION_DEVICE,
    AppEventBus,
    ApplicationState,
    MainGUI,
    OrbitSnapshot,
    PanelContext,
    RECEIVER_SAMPLE_RATE_MAX_HZ,
    RECEIVER_SAMPLE_RATE_MIN_HZ,
    RECEIVER_SAMPLE_RATE_STEP_HZ,
    ReceiverLocation,
    ReceiverSettings,
    ReceiverSnapshot,
    ReceiverStatus,
    SatelliteDefinition,
    SatellitePosition,
    SpectrumFrame,
    TRACKED_SATELLITES,
    TLERecord,
    configure_application_style,
    normalize_appearance_mode,
)
from panel_log import ApplicationLogController


logger = logging.getLogger("meteor_m.main")
settings_logger = logging.getLogger("meteor_m.settings")
receiver_logger = logging.getLogger("meteor_m.receiver")
tle_logger = logging.getLogger("meteor_m.tle_provider")
tracking_logger = logging.getLogger("meteor_m.tracking_service")


class SettingsError(RuntimeError):
    pass


DEFAULT_DATA: dict[str, Any] = {
    "schema_version": 1,
    "receiver": {
        "location": {"latitude_deg": 52.52, "longitude_deg": 13.405},
        "device": SIMULATION_DEVICE,
        "center_frequency_hz": 137_900_000.0,
        "sample_rate_hz": 2_000_000.0,
        "filter_bandwidth_hz": 1_750_000.0,
        "lna_gain_db": 24,
        "vga_gain_db": 28,
        "amplifier_enabled": False,
    },
    "satellite": {
        "enabled_norad_catalog_ids": [57166, 59051],
        "position_update_seconds": 1.0,
        "ground_track_refresh_seconds": 60.0,
        "tle_refresh_hours": 6.0,
        "tle_minimum_request_hours": 2.0,
    },
    "interface": {
        "log_verbosity": "INFO",
        "appearance_mode": "green",
        "window_width": 1480,
        "window_height": 920,
        "loaded_panels": [],
    },
    "cache": {"tle": {}},
}


class SettingsStore:
    """Read and atomically update the repository-root settings.yaml."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parent.parent / "settings.yaml"
        self._lock = threading.RLock()
        self._data = self._load()

    def receiver_settings(self) -> ReceiverSettings:
        with self._lock:
            raw = self._data["receiver"]
            requested_sample_rate = float(raw["sample_rate_hz"])
            sample_rate_hz = min(
                RECEIVER_SAMPLE_RATE_MAX_HZ,
                max(
                    RECEIVER_SAMPLE_RATE_MIN_HZ,
                    round(requested_sample_rate / RECEIVER_SAMPLE_RATE_STEP_HZ)
                    * RECEIVER_SAMPLE_RATE_STEP_HZ,
                ),
            )
            if sample_rate_hz != requested_sample_rate:
                settings_logger.warning(
                    "Adjusted unsupported sample rate %.3f MS/s to %.0f MS/s",
                    requested_sample_rate / 1e6,
                    sample_rate_hz / 1e6,
                )
            return ReceiverSettings(
                center_frequency_hz=float(raw["center_frequency_hz"]),
                sample_rate_hz=sample_rate_hz,
                filter_bandwidth_hz=float(raw["filter_bandwidth_hz"]),
                lna_gain_db=int(raw["lna_gain_db"]),
                vga_gain_db=int(raw["vga_gain_db"]),
                amplifier_enabled=bool(raw["amplifier_enabled"]),
                device=str(raw["device"]),
            )

    def receiver_location(self) -> ReceiverLocation:
        with self._lock:
            raw = self._data["receiver"]["location"]
            return ReceiverLocation(
                latitude_deg=float(raw["latitude_deg"]),
                longitude_deg=float(raw["longitude_deg"]),
            )

    def satellite_settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["satellite"])

    def enabled_satellite_ids(self) -> set[int]:
        with self._lock:
            known_ids = {
                satellite.norad_catalog_id for satellite in TRACKED_SATELLITES
            }
            raw = self._data["satellite"].get(
                "enabled_norad_catalog_ids", sorted(known_ids)
            )
            if not isinstance(raw, list):
                return set(known_ids)
            enabled: set[int] = set()
            for item in raw:
                try:
                    catalog_id = int(item)
                except (TypeError, ValueError):
                    settings_logger.warning(
                        "Ignoring invalid enabled satellite ID %r", item
                    )
                    continue
                if catalog_id in known_ids:
                    enabled.add(catalog_id)
            return enabled

    def log_verbosity(self) -> str:
        with self._lock:
            value = str(self._data["interface"]["log_verbosity"]).upper()
            return value if value in {"ERROR", "WARNING", "INFO", "DEBUG"} else "INFO"

    def appearance_mode(self) -> str:
        with self._lock:
            value = str(self._data["interface"]["appearance_mode"]).lower()
            return normalize_appearance_mode(value)

    def window_size(self) -> tuple[int, int]:
        with self._lock:
            interface = self._data["interface"]
            return int(interface["window_width"]), int(interface["window_height"])

    def loaded_panels(self) -> tuple[str, ...]:
        with self._lock:
            raw = self._data["interface"].get("loaded_panels", [])
            if not isinstance(raw, list):
                return ()
            return tuple(str(item) for item in raw)

    def update_receiver(self, settings: ReceiverSettings) -> None:
        with self._lock:
            self._data["receiver"].update(
                {
                    "device": settings.device,
                    "center_frequency_hz": settings.center_frequency_hz,
                    "sample_rate_hz": settings.sample_rate_hz,
                    "filter_bandwidth_hz": settings.filter_bandwidth_hz,
                    "lna_gain_db": settings.lna_gain_db,
                    "vga_gain_db": settings.vga_gain_db,
                    "amplifier_enabled": settings.amplifier_enabled,
                }
            )
            self._save_locked()

    def update_location(self, location: ReceiverLocation) -> None:
        with self._lock:
            self._data["receiver"]["location"] = {
                "latitude_deg": location.latitude_deg,
                "longitude_deg": location.longitude_deg,
            }
            self._save_locked()

    def update_log_verbosity(self, verbosity: str) -> None:
        normalized = verbosity.upper()
        if normalized not in {"ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"unsupported log verbosity: {verbosity}")
        with self._lock:
            self._data["interface"]["log_verbosity"] = normalized
            self._save_locked()

    def update_appearance_mode(self, appearance_mode: str) -> None:
        normalized = appearance_mode.lower()
        if normalized not in APPEARANCE_MODES:
            raise ValueError(f"unsupported appearance mode: {appearance_mode}")
        with self._lock:
            self._data["interface"]["appearance_mode"] = normalized
            self._save_locked()

    def update_window_size(self, width: int, height: int) -> None:
        with self._lock:
            self._data["interface"]["window_width"] = max(1050, int(width))
            self._data["interface"]["window_height"] = max(700, int(height))
            self._save_locked()

    def update_loaded_panels(self, panel_keys: tuple[str, ...]) -> None:
        with self._lock:
            self._data["interface"]["loaded_panels"] = list(panel_keys)
            self._save_locked()

    def update_enabled_satellites(self, catalog_ids: set[int]) -> None:
        known_ids = {
            satellite.norad_catalog_id for satellite in TRACKED_SATELLITES
        }
        with self._lock:
            self._data["satellite"]["enabled_norad_catalog_ids"] = sorted(
                int(item) for item in catalog_ids if int(item) in known_ids
            )
            self._data["satellite"].pop("selected_norad_catalog_id", None)
            self._save_locked()

    def load_tle(self, norad_catalog_id: int) -> dict[str, Any] | None:
        with self._lock:
            raw = self._data["cache"]["tle"].get(str(norad_catalog_id))
            return copy.deepcopy(raw) if isinstance(raw, dict) else None

    def save_tle(self, norad_catalog_id: int, record: dict[str, Any]) -> None:
        with self._lock:
            self._data["cache"]["tle"][str(norad_catalog_id)] = copy.deepcopy(record)
            self._save_locked()

    @property
    def description(self) -> str:
        return str(self.path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            data = copy.deepcopy(DEFAULT_DATA)
            self._data = data
            self._save_locked()
            return data
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            raise SettingsError(f"could not read {self.path}: {error}") from error
        if not isinstance(loaded, dict):
            raise SettingsError(f"{self.path} must contain a YAML mapping")
        return self._merge_defaults(DEFAULT_DATA, loaded)

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise SettingsError(f"could not write {self.path}: {error}") from error

    @classmethod
    def _merge_defaults(
        cls, defaults: dict[str, Any], loaded: dict[str, Any]
    ) -> dict[str, Any]:
        result = copy.deepcopy(loaded)
        for key, default in defaults.items():
            if key not in result:
                result[key] = copy.deepcopy(default)
            elif isinstance(default, dict):
                if not isinstance(result[key], dict):
                    result[key] = copy.deepcopy(default)
                else:
                    result[key] = cls._merge_defaults(default, result[key])
        return result


class SpectrumProcessor:
    def __init__(self, fft_size: int = 2048, floor_db: float = -130.0) -> None:
        if fft_size < 16 or fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two and at least 16")
        self.fft_size = fft_size
        self.floor_db = floor_db
        self._window = np.hanning(fft_size).astype(np.float32)
        self._window_gain = float(np.sum(self._window))

    def prepare(
        self,
        iq: NDArray[np.complexfloating],
        sample_rate_hz: float,
        center_frequency_hz: float,
    ) -> SpectrumFrame:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if iq.size < self.fft_size:
            raise ValueError(f"at least {self.fft_size} IQ samples are required")
        windowed = np.asarray(iq[-self.fft_size :], dtype=np.complex64) * self._window
        bins = np.fft.fftshift(np.fft.fft(windowed))
        magnitude = np.maximum(
            np.abs(bins) / self._window_gain, 10 ** (self.floor_db / 20)
        )
        power_db = (20.0 * np.log10(magnitude)).astype(np.float64, copy=False)
        np.maximum(power_db, self.floor_db, out=power_db)
        frequency_hz = center_frequency_hz + np.fft.fftshift(
            np.fft.fftfreq(self.fft_size, d=1.0 / sample_rate_hz)
        )
        frequency_hz.setflags(write=False)
        power_db.setflags(write=False)
        return SpectrumFrame(frequency_hz=frequency_hz, power_db=power_db)


class ConstellationProcessor:
    def __init__(self, max_points: int = 1200) -> None:
        if max_points < 1:
            raise ValueError("max_points must be positive")
        self.max_points = max_points

    def prepare(self, iq: NDArray[np.complexfloating]) -> NDArray[np.complex64]:
        if iq.size == 0:
            result = np.empty(0, dtype=np.complex64)
            result.setflags(write=False)
            return result
        stride = max(1, iq.size // self.max_points)
        points = np.asarray(
            iq[::stride][: self.max_points], dtype=np.complex64
        ).copy()
        scale = float(np.percentile(np.abs(points), 95))
        if scale > 1e-9:
            points /= scale
        points.setflags(write=False)
        return points


class ReceiverBackend(ABC):
    """Common lifecycle and latest-value contract for receiver backends."""

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @property
    @abstractmethod
    def status(self) -> ReceiverStatus: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self, timeout: float = 2.0) -> None: ...

    @abstractmethod
    def update_settings(self, settings: ReceiverSettings) -> None: ...

    @abstractmethod
    def take_latest(self) -> ReceiverSnapshot | None: ...


class SimulationBackend(ReceiverBackend):
    """Hardware-free receiver backend with a bounded latest-snapshot queue."""

    def __init__(self, update_rate_hz: float = 12.0, queue_size: int = 2) -> None:
        if update_rate_hz <= 0 or queue_size < 1:
            raise ValueError("update rate and queue size must be positive")
        self._period = 1.0 / update_rate_hz
        self._snapshots: queue.Queue[ReceiverSnapshot] = queue.Queue(maxsize=queue_size)
        self._settings = ReceiverSettings()
        self._settings_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._spectrum = SpectrumProcessor()
        self._constellation = ConstellationProcessor()
        self._rng = np.random.default_rng(23)
        self._image = np.zeros((420, 560, 3), dtype=np.uint8)
        self._status_lock = threading.Lock()
        self._status = ReceiverStatus()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> ReceiverStatus:
        with self._status_lock:
            return self._status

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="meteor-m-simulation", daemon=True
        )
        self._thread.start()
        self._set_status("running", "Simulated IQ stream", 0)
        receiver_logger.info("Started simulated receive pipeline")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._set_status("stopped", "Receiver stopped", self.status.samples_received)
        receiver_logger.info("Stopped simulated receive pipeline")

    def update_settings(self, settings: ReceiverSettings) -> None:
        with self._settings_lock:
            self._settings = settings
        receiver_logger.info(
            "Receiver settings applied: %.3f MHz, %.2f MS/s, LNA %d dB, VGA %d dB",
            settings.center_frequency_hz / 1e6,
            settings.sample_rate_hz / 1e6,
            settings.lna_gain_db,
            settings.vga_gain_db,
        )

    def take_latest(self) -> ReceiverSnapshot | None:
        latest = None
        while True:
            try:
                latest = self._snapshots.get_nowait()
            except queue.Empty:
                return latest

    def _publish(self, snapshot: ReceiverSnapshot) -> None:
        try:
            self._snapshots.put_nowait(snapshot)
            return
        except queue.Full:
            pass
        try:
            self._snapshots.get_nowait()
        except queue.Empty:
            pass
        self._snapshots.put_nowait(snapshot)

    def _run(self) -> None:
        started = time.monotonic()
        frame_number = 0
        while not self._stop_event.is_set():
            tick_started = time.monotonic()
            elapsed = tick_started - started
            with self._settings_lock:
                settings = self._settings
            iq = self._make_iq(elapsed)
            spectrum = self._spectrum.prepare(
                iq, settings.sample_rate_hz, settings.center_frequency_hz
            )
            constellation = self._constellation.prepare(iq)
            image_lines = min(
                self._image.shape[0],
                int(elapsed * 8) % (self._image.shape[0] + 80),
            )
            image = None
            if frame_number % 4 == 0:
                self._update_image(image_lines, elapsed)
                image = self._image.copy()
                image.setflags(write=False)
            self._publish(
                ReceiverSnapshot(
                    created_at=datetime.now(UTC),
                    spectrum=spectrum,
                    constellation=constellation,
                    image_rgb=image,
                    image_lines=image_lines,
                    signal_level_dbfs=-48.0 + 3.0 * math.sin(elapsed / 2.5),
                    symbol_sync=elapsed % 18.0 > 1.5,
                    frame_sync=elapsed % 18.0 > 3.0,
                    packets_decoded=max(0, int((elapsed - 3.0) * 6.5)),
                )
            )
            self._set_status(
                "running", "Simulated IQ stream", (frame_number + 1) * iq.size
            )
            frame_number += 1
            self._stop_event.wait(
                max(0.0, self._period - (time.monotonic() - tick_started))
            )

    def _make_iq(self, elapsed: float) -> NDArray[np.complex64]:
        count = 4096
        symbols = self._rng.integers(0, 4, count)
        qpsk = np.exp(1j * (np.pi / 4 + symbols * np.pi / 2))
        phase = np.exp(1j * (elapsed * 0.7 + np.arange(count) * 0.002))
        noise = self._rng.normal(0, 0.18, count) + 1j * self._rng.normal(
            0, 0.18, count
        )
        return np.asarray(0.72 * qpsk * phase + noise, dtype=np.complex64)

    def _update_image(self, visible_lines: int, elapsed: float) -> None:
        self._image.fill(8)
        if visible_lines <= 0:
            return
        height, width, _ = self._image.shape
        y, x = np.mgrid[:visible_lines, :width]
        cloud = np.sin(x / 24.0 + y / 37.0 + elapsed * 0.06)
        cloud += 0.55 * np.sin(x / 9.0 - y / 29.0)
        terrain = 0.5 + 0.5 * np.sin(x / 70.0 + np.cos(y / 33.0) * 2.2)
        self._image[:visible_lines, :, 0] = np.clip(
            72 + 68 * cloud + 36 * terrain, 0, 255
        ).astype(np.uint8)
        self._image[:visible_lines, :, 1] = np.clip(
            92 + 55 * cloud + 55 * terrain, 0, 255
        ).astype(np.uint8)
        self._image[:visible_lines, :, 2] = np.clip(
            108 + 82 * cloud - 28 * terrain, 0, 255
        ).astype(np.uint8)
        if visible_lines < height:
            self._image[visible_lines : min(height, visible_lines + 2), :, :] = (
                80,
                204,
                255,
            )

    def _set_status(self, state: str, message: str, samples_received: int) -> None:
        with self._status_lock:
            self._status = ReceiverStatus(
                state=state,
                backend="Simulation",
                device=SIMULATION_DEVICE,
                message=message,
                samples_received=samples_received,
            )


class ReceiverError(RuntimeError):
    pass


class GNUradioBridge:
    """Receive-only GNU Radio/Soapy HackRF flowgraph with bounded IQ delivery."""

    def __init__(
        self,
        settings: ReceiverSettings,
        *,
        block_size: int = 4096,
        update_rate_hz: float = 12.0,
        queue_size: int = 2,
    ) -> None:
        if block_size < 2048 or update_rate_hz <= 0 or queue_size < 1:
            raise ValueError("invalid GNU Radio bridge buffering configuration")
        self.block_size = block_size
        self._update_rate_hz = update_rate_hz
        self._period = 1.0 / update_rate_hz
        self._iq_blocks: queue.Queue[NDArray[np.complex64]] = queue.Queue(
            maxsize=queue_size
        )
        self._last_accepted = 0.0
        self._pending = np.empty(block_size, dtype=np.complex64)
        self._pending_count = 0
        self._dropped_buffers = 0
        self._samples_received = 0
        self._top_block = None
        self._source = None
        self._vectorizer = None
        self._analysis_decimator = None
        self._sink = None
        self._build(settings)

    @property
    def samples_received(self) -> int:
        return self._samples_received

    @property
    def dropped_buffers(self) -> int:
        return self._dropped_buffers

    def _build(self, settings: ReceiverSettings) -> None:
        self._validate_sample_rate(settings.sample_rate_hz)
        try:
            from gnuradio import blocks, gr, soapy
        except (ImportError, OSError) as error:
            raise ReceiverError(
                "GNU Radio with the gr-soapy component is not available"
            ) from error

        bridge = self

        class LatestIQSink(gr.sync_block):
            def __init__(self) -> None:
                super().__init__(
                    name="meteor_m_latest_iq_sink",
                    in_sig=[(np.complex64, bridge.block_size)],
                    out_sig=None,
                )

            def work(self, input_items, output_items):
                del output_items
                vectors = input_items[0]
                if len(vectors):
                    bridge._accept_samples(vectors[-1])
                return len(vectors)

        device_args = self._device_args(settings.device)
        try:
            self._top_block = gr.top_block("meteor_m_hackrf_receive")
            self._source = soapy.source(
                "driver=hackrf", "fc32", 1, device_args, "", [""], [""]
            )
            self._configure_source(self._source, settings)
            self._vectorizer = blocks.stream_to_vector(
                gr.sizeof_gr_complex, self.block_size
            )
            vectors_per_update = self.analysis_decimation(
                settings.sample_rate_hz, self.block_size, self._update_rate_hz
            )
            self._analysis_decimator = blocks.keep_one_in_n(
                gr.sizeof_gr_complex * self.block_size, vectors_per_update
            )
            self._sink = LatestIQSink()
            self._top_block.connect(
                self._source,
                self._vectorizer,
                self._analysis_decimator,
                self._sink,
            )
        except Exception as error:
            self._top_block = None
            self._source = None
            self._vectorizer = None
            self._analysis_decimator = None
            self._sink = None
            raise ReceiverError(
                f"could not construct HackRF flowgraph: {error}"
            ) from error

    @staticmethod
    def _configure_source(source: object, settings: ReceiverSettings) -> None:
        source.set_sample_rate(0, settings.sample_rate_hz)
        GNUradioBridge._configure_runtime_source(source, settings)

    @staticmethod
    def _configure_runtime_source(source: object, settings: ReceiverSettings) -> None:
        source.set_bandwidth(0, settings.filter_bandwidth_hz)
        source.set_frequency(0, settings.center_frequency_hz)
        source.set_gain(0, "AMP", settings.amplifier_enabled)
        source.set_gain(0, "LNA", settings.lna_gain_db)
        source.set_gain(0, "VGA", settings.vga_gain_db)

    @staticmethod
    def _validate_sample_rate(sample_rate_hz: float) -> None:
        if not (
            RECEIVER_SAMPLE_RATE_MIN_HZ
            <= sample_rate_hz
            <= RECEIVER_SAMPLE_RATE_MAX_HZ
        ):
            raise ReceiverError(
                "Sample rate must be between 2 and 20 MS/s for this application"
            )
        if sample_rate_hz % RECEIVER_SAMPLE_RATE_STEP_HZ:
            raise ReceiverError("Sample rate must use full 1 MHz steps")

    @staticmethod
    def analysis_decimation(
        sample_rate_hz: float, block_size: int, update_rate_hz: float
    ) -> int:
        return max(1, round(sample_rate_hz / (block_size * update_rate_hz)))

    @staticmethod
    def _device_args(device: str) -> str:
        match = re.fullmatch(r"HackRF One \[([0-9A-Fa-f]+)\]", device.strip())
        return f"serial={match.group(1)}" if match else ""

    def start(self) -> None:
        if self._top_block is None:
            raise ReceiverError("HackRF flowgraph is not available")
        self._top_block.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._top_block is not None:
            top_block = self._top_block

            def shutdown_flowgraph() -> None:
                top_block.stop()
                top_block.wait()

            shutdown = threading.Thread(
                target=shutdown_flowgraph,
                name="meteor-m-gnuradio-stop",
                daemon=True,
            )
            shutdown.start()
            shutdown.join(timeout)
            if shutdown.is_alive():
                raise ReceiverError("GNU Radio flowgraph did not stop in time")

    def update_settings(self, settings: ReceiverSettings) -> None:
        if self._source is None:
            raise ReceiverError("HackRF source is not available")
        self._configure_runtime_source(self._source, settings)

    def take_latest(self) -> NDArray[np.complex64] | None:
        latest = None
        while True:
            try:
                latest = self._iq_blocks.get_nowait()
            except queue.Empty:
                return latest

    def _accept_samples(self, samples: NDArray[np.complex64]) -> None:
        self._samples_received += int(samples.size)
        now = time.monotonic()
        if self._pending_count:
            needed = self.block_size - self._pending_count
            copied = min(needed, int(samples.size))
            self._pending[self._pending_count : self._pending_count + copied] = samples[
                :copied
            ]
            self._pending_count += copied
            if self._pending_count == self.block_size:
                self._queue_analysis_block(self._pending.copy(), now)
                self._pending_count = 0
            return
        if samples.size < self.block_size:
            self._pending[: samples.size] = samples
            self._pending_count = int(samples.size)
            return
        self._queue_analysis_block(
            np.asarray(samples[-self.block_size :], dtype=np.complex64).copy(), now
        )

    def _queue_analysis_block(
        self, block: NDArray[np.complex64], accepted_at: float
    ) -> None:
        self._last_accepted = accepted_at
        block.setflags(write=False)
        try:
            self._iq_blocks.put_nowait(block)
            return
        except queue.Full:
            self._dropped_buffers += 1
        try:
            self._iq_blocks.get_nowait()
        except queue.Empty:
            pass
        self._iq_blocks.put_nowait(block)


def _run_gnuradio_process(
    settings: ReceiverSettings,
    block_size: int,
    update_rate_hz: float,
    iq_queue: Any,
    command_queue: Any,
    event_queue: Any,
    stop_event: Any,
) -> None:
    """Own all GNU Radio/Soapy objects in a disposable child process."""
    bridge = None
    try:
        bridge = GNUradioBridge(
            settings,
            block_size=block_size,
            update_rate_hz=update_rate_hz,
        )
        bridge.start()
        event_queue.put(("ready", ""))
        while not stop_event.is_set():
            while True:
                try:
                    settings = command_queue.get_nowait()
                except queue.Empty:
                    break
                bridge.update_settings(settings)
            iq = bridge.take_latest()
            if iq is None:
                stop_event.wait(0.01)
                continue
            item = (iq, bridge.samples_received, bridge.dropped_buffers)
            try:
                iq_queue.put_nowait(item)
            except queue.Full:
                try:
                    iq_queue.get(timeout=0.01)
                except queue.Empty:
                    pass
                try:
                    iq_queue.put_nowait(item)
                except queue.Full:
                    pass
    except Exception as error:
        try:
            event_queue.put_nowait(("error", str(error)))
        except queue.Full:
            pass
    finally:
        if bridge is not None:
            bridge.stop()


class GNUradioProcessBridge:
    """Restartable process boundary around the native GNU Radio bridge."""

    def __init__(
        self,
        settings: ReceiverSettings,
        *,
        block_size: int = 4096,
        update_rate_hz: float = 12.0,
        startup_timeout: float = 8.0,
    ) -> None:
        GNUradioBridge._validate_sample_rate(settings.sample_rate_hz)
        self._settings = settings
        self._block_size = block_size
        self._update_rate_hz = update_rate_hz
        self._startup_timeout = startup_timeout
        self._process = None
        self._iq_queue = None
        self._command_queue = None
        self._event_queue = None
        self._stop_event = None
        self._samples_received = 0
        self._dropped_buffers = 0

    @property
    def samples_received(self) -> int:
        return self._samples_received

    @property
    def dropped_buffers(self) -> int:
        return self._dropped_buffers

    def start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        context = multiprocessing.get_context("spawn")
        self._iq_queue = context.Queue(maxsize=2)
        self._command_queue = context.Queue(maxsize=4)
        self._event_queue = context.Queue(maxsize=4)
        self._stop_event = context.Event()
        self._process = context.Process(
            target=_run_gnuradio_process,
            args=(
                self._settings,
                self._block_size,
                self._update_rate_hz,
                self._iq_queue,
                self._command_queue,
                self._event_queue,
                self._stop_event,
            ),
            name="meteor-m-gnuradio",
            daemon=True,
        )
        self._process.start()
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            try:
                kind, message = self._event_queue.get(timeout=0.05)
            except queue.Empty:
                if not self._process.is_alive():
                    self.stop()
                    raise ReceiverError("GNU Radio receiver process exited during startup")
                continue
            if kind == "ready":
                return
            self.stop()
            raise ReceiverError(message or "GNU Radio receiver process failed")
        self.stop()
        raise ReceiverError("GNU Radio receiver process timed out during startup")

    def stop(self, timeout: float = 2.0) -> None:
        process = self._process
        if process is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        process.join(timeout)
        if process.is_alive():
            receiver_logger.warning(
                "Terminating unresponsive GNU Radio receiver process"
            )
            process.terminate()
            process.join(1.0)
        if process.is_alive():
            process.kill()
            process.join(1.0)
        self._process = None
        self._close_queues()

    def update_settings(self, settings: ReceiverSettings) -> None:
        self._settings = settings
        if self._command_queue is None:
            return
        try:
            self._command_queue.put_nowait(settings)
        except queue.Full:
            try:
                self._command_queue.get(timeout=0.01)
            except queue.Empty:
                pass
            try:
                self._command_queue.put_nowait(settings)
            except queue.Full:
                pass

    def take_latest(self) -> NDArray[np.complex64] | None:
        self._raise_process_error()
        latest = None
        if self._iq_queue is None:
            return None
        while True:
            try:
                latest, self._samples_received, self._dropped_buffers = (
                    self._iq_queue.get_nowait()
                )
            except queue.Empty:
                return latest

    def _raise_process_error(self) -> None:
        if self._event_queue is not None:
            try:
                kind, message = self._event_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                if kind == "error":
                    raise ReceiverError(message)
        if self._process is not None and not self._process.is_alive():
            raise ReceiverError("GNU Radio receiver process exited unexpectedly")

    def _close_queues(self) -> None:
        for transport in (
            self._iq_queue,
            self._command_queue,
            self._event_queue,
        ):
            if transport is not None:
                transport.close()
                transport.cancel_join_thread()
        self._iq_queue = None
        self._command_queue = None
        self._event_queue = None
        self._stop_event = None


class HackRFController(ReceiverBackend):
    """Discover and run a HackRF bridge entirely outside the GUI thread."""

    SERIAL_PATTERN = re.compile(r"Serial number:\s*([0-9A-Fa-f]+)")

    def __init__(
        self,
        *,
        bridge_factory: Callable[[ReceiverSettings], Any] = GNUradioProcessBridge,
        device_probe: Callable[[], tuple[str, ...]] | None = None,
        sample_timeout: float = 3.0,
    ) -> None:
        if sample_timeout <= 0:
            raise ValueError("sample_timeout must be positive")
        self._bridge_factory = bridge_factory
        self._device_probe = device_probe or self.discover_devices
        self._sample_timeout = sample_timeout
        self._settings = ReceiverSettings(device=HACKRF_AUTO_DEVICE)
        self._settings_lock = threading.Lock()
        self._settings_revision = 0
        self._snapshots: queue.Queue[ReceiverSnapshot] = queue.Queue(maxsize=2)
        self._status_lock = threading.Lock()
        self._status = ReceiverStatus(
            backend="HackRF / GNU Radio",
            device=HACKRF_AUTO_DEVICE,
            message="Hardware receiver stopped",
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._spectrum = SpectrumProcessor()
        self._constellation = ConstellationProcessor()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> ReceiverStatus:
        with self._status_lock:
            return self._status

    @classmethod
    def discover_devices(cls, timeout: float = 4.0) -> tuple[str, ...]:
        try:
            result = subprocess.run(
                ["hackrf_info"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise ReceiverError("hackrf_info is not installed") from error
        except subprocess.TimeoutExpired as error:
            raise ReceiverError("HackRF discovery timed out") from error
        output = f"{result.stdout}\n{result.stderr}"
        serials = tuple(dict.fromkeys(cls.SERIAL_PATTERN.findall(output)))
        if result.returncode != 0 or not serials:
            detail = next(
                (line.strip() for line in reversed(output.splitlines()) if line.strip()),
                "no HackRF One detected",
            )
            raise ReceiverError(f"HackRF discovery failed: {detail}")
        return tuple(f"HackRF One [{serial}]" for serial in serials)

    def probe_devices(self) -> tuple[str, ...]:
        return self._device_probe()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._set_status("discovering", "Looking for HackRF One")
        self._thread = threading.Thread(
            target=self._run, name="meteor-m-hackrf", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 4.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self._thread is not None and self._thread.is_alive():
            self._set_status("error", "HackRF worker did not stop in time")
            return
        self._thread = None
        if self.status.state != "error":
            self._set_status("stopped", "Hardware receiver stopped")

    def update_settings(self, settings: ReceiverSettings) -> None:
        with self._settings_lock:
            self._settings = settings
            self._settings_revision += 1

    def take_latest(self) -> ReceiverSnapshot | None:
        latest = None
        while True:
            try:
                latest = self._snapshots.get_nowait()
            except queue.Empty:
                return latest

    def _run(self) -> None:
        bridge = None
        try:
            devices = self.probe_devices()
            with self._settings_lock:
                settings = self._settings
                applied_revision = self._settings_revision
            if settings.device != HACKRF_AUTO_DEVICE and settings.device not in devices:
                raise ReceiverError(
                    f"Selected device is not connected: {settings.device}"
                )
            self._set_status(
                "starting", "Constructing receive-only GNU Radio flowgraph"
            )
            bridge = self._bridge_factory(settings)
            bridge.start()
            last_sample_at = time.monotonic()
            self._set_status("running", "Live complex samples", bridge=bridge)
            while not self._stop_event.is_set():
                with self._settings_lock:
                    current_settings = self._settings
                    revision = self._settings_revision
                if revision != applied_revision:
                    if current_settings.sample_rate_hz != settings.sample_rate_hz:
                        self._set_status(
                            "starting",
                            "Restarting GNU Radio for the new sample rate",
                            bridge=bridge,
                        )
                        self._clear_snapshots()
                        bridge.stop()
                        bridge = self._bridge_factory(current_settings)
                        bridge.start()
                        last_sample_at = time.monotonic()
                    else:
                        bridge.update_settings(current_settings)
                    settings = current_settings
                    applied_revision = revision
                iq = bridge.take_latest()
                if iq is None:
                    if time.monotonic() - last_sample_at > self._sample_timeout:
                        raise ReceiverError(
                            "No IQ samples received; check the HackRF connection, "
                            "then reconnect it and press Start receiver"
                        )
                    self._stop_event.wait(0.02)
                    continue
                last_sample_at = time.monotonic()
                rms = float(np.sqrt(np.mean(np.square(np.abs(iq)))))
                signal_level = 20.0 * math.log10(max(rms, 1e-12))
                self._publish(
                    ReceiverSnapshot(
                        created_at=datetime.now(UTC),
                        spectrum=self._spectrum.prepare(
                            iq, settings.sample_rate_hz, settings.center_frequency_hz
                        ),
                        constellation=self._constellation.prepare(iq),
                        image_rgb=None,
                        image_lines=0,
                        signal_level_dbfs=signal_level,
                        symbol_sync=False,
                        frame_sync=False,
                        packets_decoded=0,
                    )
                )
                self._set_status("running", "Live complex samples", bridge=bridge)
        except Exception as error:
            self._set_status("error", str(error), bridge=bridge)
            receiver_logger.error("HackRF receive pipeline stopped: %s", error)
        finally:
            if bridge is not None:
                try:
                    bridge.stop()
                except Exception as error:
                    receiver_logger.warning(
                        "Could not stop GNU Radio flowgraph: %s", error
                    )

    def _publish(self, snapshot: ReceiverSnapshot) -> None:
        try:
            self._snapshots.put_nowait(snapshot)
            return
        except queue.Full:
            pass
        try:
            self._snapshots.get_nowait()
        except queue.Empty:
            pass
        self._snapshots.put_nowait(snapshot)

    def _clear_snapshots(self) -> None:
        while True:
            try:
                self._snapshots.get_nowait()
            except queue.Empty:
                return

    def _set_status(
        self, state: str, message: str, *, bridge: GNUradioBridge | None = None
    ) -> None:
        with self._settings_lock:
            device = self._settings.device
        with self._status_lock:
            self._status = ReceiverStatus(
                state=state,
                backend="HackRF / GNU Radio",
                device=device,
                message=message,
                samples_received=bridge.samples_received if bridge else 0,
                dropped_buffers=bridge.dropped_buffers if bridge else 0,
            )


class ReceiverManager:
    """Select simulation or HackRF while keeping one orchestrator contract."""

    def __init__(
        self,
        simulation: SimulationBackend | None = None,
        hackrf: HackRFController | None = None,
    ) -> None:
        self.simulation = simulation or SimulationBackend()
        self.hackrf = hackrf or HackRFController()
        self._settings = ReceiverSettings()
        self._active: ReceiverBackend = self.simulation
        self._device_updates: queue.Queue[tuple[tuple[str, ...], str | None]] = (
            queue.Queue(maxsize=1)
        )
        self._discovery_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._active.running

    @property
    def status(self) -> ReceiverStatus:
        return self._active.status

    def start(self) -> None:
        self._select_backend()
        self._active.update_settings(self._settings)
        self._active.start()

    def stop(self, timeout: float = 4.0) -> None:
        self._active.stop(timeout)

    def update_settings(self, settings: ReceiverSettings) -> None:
        was_active = self._active.status.active
        selected = self._backend_for(settings)
        if selected is not self._active and was_active:
            self._active.stop()
        self._settings = settings
        self._active = selected
        self._active.update_settings(settings)
        if was_active and not self._active.status.active:
            self._active.start()

    def take_latest(self) -> ReceiverSnapshot | None:
        return self._active.take_latest()

    def request_device_discovery(self) -> None:
        if self._discovery_thread is not None and self._discovery_thread.is_alive():
            return

        def discover() -> None:
            try:
                hardware = self.hackrf.probe_devices()
                update = ((SIMULATION_DEVICE, HACKRF_AUTO_DEVICE, *hardware), None)
            except Exception as error:
                update = ((SIMULATION_DEVICE, HACKRF_AUTO_DEVICE), str(error))
            try:
                self._device_updates.put_nowait(update)
            except queue.Full:
                try:
                    self._device_updates.get_nowait()
                except queue.Empty:
                    pass
                self._device_updates.put_nowait(update)

        self._discovery_thread = threading.Thread(
            target=discover, name="meteor-m-hackrf-discovery", daemon=True
        )
        self._discovery_thread.start()

    def take_device_update(self) -> tuple[tuple[str, ...], str | None] | None:
        try:
            return self._device_updates.get_nowait()
        except queue.Empty:
            return None

    def _select_backend(self) -> None:
        self._active = self._backend_for(self._settings)

    def _backend_for(self, settings: ReceiverSettings) -> ReceiverBackend:
        return (
            self.hackrf
            if settings.device.startswith("HackRF One")
            else self.simulation
        )


class TLEProviderError(RuntimeError):
    pass


class TLEProvider(ABC):
    @abstractmethod
    def get_cached_tle(self, satellite: SatelliteDefinition) -> TLERecord | None: ...

    @abstractmethod
    def get_tle(
        self, satellite: SatelliteDefinition, *, force_refresh: bool = False
    ) -> TLERecord: ...


class CelestrakTLEProvider(TLEProvider):
    BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

    def __init__(
        self,
        cache: SettingsStore,
        *,
        refresh_interval: timedelta = timedelta(hours=6),
        request_timeout: float = 10.0,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] | None = None,
        minimum_request_interval: timedelta = timedelta(hours=2),
    ) -> None:
        if minimum_request_interval < timedelta(hours=2):
            raise ValueError("minimum interval must respect CelesTrak's two-hour limit")
        if refresh_interval < minimum_request_interval:
            raise ValueError("refresh interval must not be shorter than minimum interval")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.cache = cache
        self.refresh_interval = refresh_interval
        self.request_timeout = request_timeout
        self.minimum_request_interval = minimum_request_interval
        self._opener = opener
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_tle(
        self, satellite: SatelliteDefinition, *, force_refresh: bool = False
    ) -> TLERecord:
        now = self._aware_utc(self._clock())
        cached = self._read_cached(satellite)
        if cached is not None:
            age = max(timedelta(0), now - cached.retrieved_at)
            if force_refresh and age < self.minimum_request_interval:
                tle_logger.info(
                    "TLE refresh skipped for %s: cached data is only %s old",
                    satellite.display_name,
                    self._format_age(age),
                )
                return cached
            if not force_refresh and age < self.refresh_interval:
                return cached
        url = self._url_for(satellite)
        tle_logger.info("Requesting orbital elements for %s", satellite.display_name)
        try:
            record = self._download(satellite, url, now)
            self._write_cached(record)
            tle_logger.info("Stored validated orbital elements in %s", self.cache.description)
            return record
        except TLEProviderError as error:
            if cached is not None:
                tle_logger.warning("TLE refresh failed; using validated cache: %s", error)
                return cached
            raise

    def get_cached_tle(self, satellite: SatelliteDefinition) -> TLERecord | None:
        return self._read_cached(satellite)

    def _download(
        self, satellite: SatelliteDefinition, url: str, retrieved_at: datetime
    ) -> TLERecord:
        request = Request(
            url,
            headers={"User-Agent": "Meteor-M-LRPT/0.1 (+desktop receive-only client)"},
        )
        try:
            with self._opener(request, timeout=self.request_timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise TLEProviderError(f"CelesTrak returned HTTP {status}")
                payload = response.read(16_385)
        except HTTPError as error:
            raise TLEProviderError(f"CelesTrak returned HTTP {error.code}") from error
        except (URLError, OSError, TimeoutError) as error:
            raise TLEProviderError(f"could not contact CelesTrak: {error}") from error
        if len(payload) > 16_384:
            raise TLEProviderError("CelesTrak response exceeded the expected size")
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as error:
            raise TLEProviderError("CelesTrak returned non-ASCII TLE data") from error
        return self._parse_tle(satellite, text, retrieved_at, url, from_cache=False)

    def _read_cached(self, satellite: SatelliteDefinition) -> TLERecord | None:
        raw = self.cache.load_tle(satellite.norad_catalog_id)
        if raw is None:
            return None
        try:
            retrieved_at = self._aware_utc(datetime.fromisoformat(str(raw["retrieved_at"])))
            text = f"{raw['name']}\n{raw['line1']}\n{raw['line2']}\n"
            return self._parse_tle(
                satellite,
                text,
                retrieved_at,
                str(raw.get("source_url", self._url_for(satellite))),
                from_cache=True,
            )
        except (KeyError, TypeError, ValueError, TLEProviderError) as error:
            tle_logger.warning("Ignoring invalid cached TLE: %s", error)
            return None

    def _write_cached(self, record: TLERecord) -> None:
        self.cache.save_tle(
            record.satellite.norad_catalog_id,
            {
                "name": record.name,
                "retrieved_at": record.retrieved_at.isoformat(),
                "source_url": record.source_url,
                "line1": record.line1,
                "line2": record.line2,
            },
        )

    @classmethod
    def _parse_tle(
        cls,
        satellite: SatelliteDefinition,
        text: str,
        retrieved_at: datetime,
        source_url: str,
        *,
        from_cache: bool,
    ) -> TLERecord:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if len(lines) == 2:
            name = satellite.display_name
            line1, line2 = lines
        elif len(lines) == 3:
            name, line1, line2 = lines
        else:
            raise TLEProviderError("expected a two- or three-line TLE response")
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            raise TLEProviderError("response does not contain valid TLE line prefixes")
        if len(line1) != 69 or len(line2) != 69:
            raise TLEProviderError("TLE lines must each contain exactly 69 characters")
        try:
            line1_catalog = int(line1[2:7])
            line2_catalog = int(line2[2:7])
        except ValueError as error:
            raise TLEProviderError("TLE catalog number is invalid") from error
        if line1_catalog != satellite.norad_catalog_id or line2_catalog != line1_catalog:
            raise TLEProviderError("TLE catalog number does not match requested satellite")
        if not cls._checksum_valid(line1) or not cls._checksum_valid(line2):
            raise TLEProviderError("TLE checksum validation failed")
        return TLERecord(
            satellite=satellite,
            name=name.strip(),
            line1=line1,
            line2=line2,
            retrieved_at=cls._aware_utc(retrieved_at),
            source_url=source_url,
            from_cache=from_cache,
        )

    @staticmethod
    def _checksum_valid(line: str) -> bool:
        if not line[68].isdigit():
            return False
        checksum = sum(int(character) for character in line[:68] if character.isdigit())
        checksum += line[:68].count("-")
        return checksum % 10 == int(line[68])

    @classmethod
    def _url_for(cls, satellite: SatelliteDefinition) -> str:
        return f"{cls.BASE_URL}?{urlencode({'CATNR': satellite.norad_catalog_id, 'FORMAT': 'TLE'})}"

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _format_age(age: timedelta) -> str:
        seconds = max(0, int(age.total_seconds()))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"


class SatelliteTracker:
    """Perform local SGP4 propagation with no file or network I/O."""

    def __init__(self, tle: TLERecord) -> None:
        self._timescale = load.timescale(builtin=True)
        self._satellite = EarthSatellite(tle.line1, tle.line2, tle.name, ts=self._timescale)

    @property
    def tle_epoch(self) -> datetime:
        return self._satellite.epoch.utc_datetime().astimezone(UTC)

    def position_at(self, observed_at: datetime | None = None) -> SatellitePosition:
        when = self._as_utc(observed_at or datetime.now(UTC))
        geographic = wgs84.geographic_position_of(
            self._satellite.at(self._timescale.from_datetime(when))
        )
        return SatellitePosition(
            observed_at=when,
            latitude_deg=float(geographic.latitude.degrees),
            longitude_deg=float(geographic.longitude.degrees),
            altitude_km=float(geographic.elevation.km),
        )

    def ground_track(
        self,
        start_at: datetime,
        *,
        duration: timedelta = timedelta(hours=3),
        step: timedelta = timedelta(minutes=2),
    ) -> tuple[SatellitePosition, ...]:
        start = self._as_utc(start_at)
        if duration < timedelta(0) or step <= timedelta(0):
            raise ValueError("duration must be non-negative and step positive")
        count = int(duration / step) + 1
        if count > 10_000:
            raise ValueError("ground track would contain too many points")
        observed = tuple(start + index * step for index in range(count))
        geographic = wgs84.geographic_position_of(
            self._satellite.at(self._timescale.from_datetimes(list(observed)))
        )
        return tuple(
            SatellitePosition(when, float(latitude), float(longitude), float(altitude))
            for when, latitude, longitude, altitude in zip(
                observed,
                geographic.latitude.degrees,
                geographic.longitude.degrees,
                geographic.elevation.km,
                strict=True,
            )
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)


class SatelliteTrackingService:
    """Retrieve cached TLE data and propagate it outside the GUI thread."""

    def __init__(
        self,
        provider: TLEProvider,
        satellite: SatelliteDefinition,
        *,
        update_interval: float = 1.0,
        track_refresh_interval: float = 60.0,
        queue_size: int = 2,
    ) -> None:
        if update_interval <= 0 or track_refresh_interval <= 0 or queue_size < 1:
            raise ValueError("tracking intervals and queue size must be positive")
        self.provider = provider
        self.satellite = satellite
        self.update_interval = update_interval
        self.track_refresh_interval = track_refresh_interval
        self._updates: queue.Queue[OrbitSnapshot] = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error_lock = threading.Lock()
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        with self._error_lock:
            return self._last_error

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._refresh_event.clear()
        self._set_error(None)
        self._thread = threading.Thread(
            target=self._run, name="meteor-m-satellite-tracking", daemon=True
        )
        self._thread.start()
        tracking_logger.info("Started tracking worker for %s", self.satellite.display_name)

    def stop(self, timeout: float = 12.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        tracking_logger.info("Stopped tracking worker for %s", self.satellite.display_name)

    def request_tle_refresh(self) -> None:
        self._refresh_event.set()
        tracking_logger.info("Manual TLE refresh requested for %s", self.satellite.display_name)

    def take_latest(self) -> OrbitSnapshot | None:
        latest = None
        while True:
            try:
                latest = self._updates.get_nowait()
            except queue.Empty:
                return latest

    def _run(self) -> None:
        tracker = None
        tle = None
        try:
            tle = self.provider.get_cached_tle(self.satellite)
            if tle is not None:
                tracker = SatelliteTracker(tle)
                tracking_logger.info("Tracking from cached TLE epoch %s", tracker.tle_epoch)
            else:
                message = (
                    "No cached orbital elements are available. Use "
                    "‘Fetch tracking data online now’."
                )
                self._set_error(message)
                tracking_logger.warning(message)
            ground_track = ()
            track_updated_at = None
            while not self._stop_event.is_set():
                if self._refresh_event.is_set():
                    self._refresh_event.clear()
                    try:
                        tle = self.provider.get_tle(self.satellite, force_refresh=True)
                        tracker = SatelliteTracker(tle)
                        ground_track = ()
                        track_updated_at = None
                        self._set_error(None)
                    except Exception as error:
                        self._set_error(str(error))
                        tracking_logger.warning("Manual TLE refresh failed: %s", error)
                if tracker is None or tle is None:
                    self._stop_event.wait(min(self.update_interval, 0.5))
                    continue
                now = datetime.now(UTC)
                if (
                    track_updated_at is None
                    or (now - track_updated_at).total_seconds()
                    >= self.track_refresh_interval
                ):
                    ground_track = tracker.ground_track(
                        now - timedelta(minutes=60),
                        duration=timedelta(hours=3),
                        step=timedelta(seconds=12),
                    )
                    track_updated_at = now
                self._publish(
                    OrbitSnapshot(
                        satellite=self.satellite,
                        position=tracker.position_at(now),
                        ground_track=ground_track,
                        tle_epoch=tracker.tle_epoch,
                        tle_retrieved_at=tle.retrieved_at,
                        using_cached_tle=tle.from_cache,
                    )
                )
                self._stop_event.wait(self.update_interval)
        except Exception as error:
            self._set_error(str(error))
            tracking_logger.error("Satellite tracking stopped: %s", error)

    def _publish(self, snapshot: OrbitSnapshot) -> None:
        try:
            self._updates.put_nowait(snapshot)
            return
        except queue.Full:
            pass
        try:
            self._updates.get_nowait()
        except queue.Empty:
            pass
        self._updates.put_nowait(snapshot)

    def _set_error(self, message: str | None) -> None:
        with self._error_lock:
            self._last_error = message


class AppOrchestrator(QObject):
    """Own services and route their state to independently loaded panels."""

    def __init__(
        self,
        gui: MainGUI,
        bus: AppEventBus,
        state: ApplicationState,
        settings: SettingsStore,
        receiver: ReceiverManager | SimulationBackend,
        tracking_services: dict[int, SatelliteTrackingService],
    ) -> None:
        super().__init__(gui)
        self.gui = gui
        self.bus = bus
        self.state = state
        self.settings = settings
        self.receiver = receiver
        self.tracking_services = tracking_services
        self._reported_tracking_errors: dict[int, str] = {}
        self._last_receiver_status: ReceiverStatus | None = None
        self.receiver_timer = QTimer(self)
        self.receiver_timer.setInterval(33)
        self.receiver_timer.timeout.connect(self._deliver_receiver_snapshot)
        self.receiver_timer.start()
        self.tracking_timer = QTimer(self)
        self.tracking_timer.setInterval(200)
        self.tracking_timer.timeout.connect(self._deliver_orbit_snapshot)
        bus.receiver_start_requested.connect(self.start_receiver)
        bus.receiver_stop_requested.connect(self.stop_receiver)
        bus.receiver_settings_requested.connect(self.apply_receiver_settings)
        bus.receiver_devices_requested.connect(self.discover_receiver_devices)
        bus.location_requested.connect(self.apply_location)
        bus.tle_refresh_requested.connect(self.refresh_tle)
        bus.satellite_enabled_requested.connect(self.set_satellite_enabled)
        bus.appearance_requested.connect(self.apply_appearance)
        gui.panel_loaded.connect(self._panel_layout_changed)
        gui.panel_unloaded.connect(self._panel_layout_changed)

    @Slot()
    def start_receiver(self) -> None:
        try:
            self.receiver.update_settings(self.state.receiver_settings)
            self.receiver.start()
        except Exception as error:
            logger.error("Could not start receiver: %s", error)
            self.state.receiver_running = False
            self.bus.receiver_running_changed.emit(False)
            return
        self.state.receiver_running = True
        self.bus.receiver_running_changed.emit(True)
        self._deliver_receiver_status(force=True)
        logger.info(
            "Receiver start requested for %s", self.state.receiver_settings.device
        )

    @Slot()
    def stop_receiver(self) -> None:
        self.receiver.stop()
        self.state.receiver_running = False
        self.bus.receiver_running_changed.emit(False)
        self._deliver_receiver_status(force=True)
        logger.info("Receiver stopped")

    @Slot(object)
    def apply_receiver_settings(self, receiver_settings: ReceiverSettings) -> None:
        self.state.receiver_settings = receiver_settings
        try:
            self.settings.update_receiver(receiver_settings)
        except Exception as error:
            logger.error("Could not save receiver settings: %s", error)
            return
        self.receiver.update_settings(receiver_settings)
        self.bus.receiver_settings_updated.emit(receiver_settings)
        self._deliver_receiver_status(force=True)

    @Slot()
    def discover_receiver_devices(self) -> None:
        request = getattr(self.receiver, "request_device_discovery", None)
        if request is None:
            self.bus.receiver_devices_updated.emit(self.state.receiver_devices)
            return
        request()
        logger.info("HackRF device discovery requested")

    @Slot(object)
    def apply_location(self, location: ReceiverLocation) -> None:
        self.state.receiver_location = location
        try:
            self.settings.update_location(location)
        except Exception as error:
            logger.error("Could not save receiver location: %s", error)
            return
        self.bus.location_updated.emit(location)
        logger.info(
            "Receiver location set to latitude %+.5f, longitude %+.5f",
            location.latitude_deg,
            location.longitude_deg,
        )

    @Slot(str)
    def apply_appearance(self, appearance_mode: str) -> None:
        normalized = appearance_mode.lower()
        if normalized not in APPEARANCE_MODES:
            logger.error("Unsupported appearance mode requested: %s", appearance_mode)
            return
        try:
            self.settings.update_appearance_mode(normalized)
        except Exception as error:
            logger.error("Could not save appearance setting: %s", error)
            return
        self.state.appearance_mode = normalized
        app = QApplication.instance()
        if app is not None:
            configure_application_style(app, normalized)
        self.bus.appearance_changed.emit(normalized)
        logger.info("Appearance changed to %s mode", normalized)

    @Slot(int)
    def refresh_tle(self, catalog_id: int) -> None:
        if not self._tracking_panel_loaded():
            logger.warning("TLE refresh ignored because tracking panel is unloaded")
            return
        service = self.tracking_services.get(catalog_id)
        if service is None:
            logger.error("TLE refresh requested for unknown NORAD ID %d", catalog_id)
            return
        if catalog_id not in self.state.enabled_satellite_ids:
            logger.warning("TLE refresh ignored because NORAD %d is disabled", catalog_id)
            return
        if not service.running:
            self._start_tracking_service(catalog_id, service)
        service.request_tle_refresh()

    @Slot(int, bool)
    def set_satellite_enabled(self, catalog_id: int, enabled: bool) -> None:
        service = self.tracking_services.get(catalog_id)
        if service is None:
            logger.error("Cannot change unknown NORAD ID %d", catalog_id)
            return
        if enabled:
            self.state.enabled_satellite_ids.add(catalog_id)
        else:
            self.state.enabled_satellite_ids.discard(catalog_id)
            self._stop_tracking_service(catalog_id, service)
            self.state.orbit_snapshots.pop(catalog_id, None)
            self.state.tracking_errors.pop(catalog_id, None)
            self._reported_tracking_errors.pop(catalog_id, None)
            self.bus.orbit_removed.emit(catalog_id)
        try:
            self.settings.update_enabled_satellites(
                self.state.enabled_satellite_ids
            )
        except Exception as error:
            logger.error("Could not save enabled satellites: %s", error)
        self.bus.satellite_enabled_changed.emit(catalog_id, enabled)
        if enabled and self._tracking_panel_loaded():
            self._start_tracking_service(catalog_id, service)
        self._update_tracking_timer_and_state()
        logger.info(
            "%s satellite tracking for NORAD %d",
            "Enabled" if enabled else "Disabled",
            catalog_id,
        )

    @Slot(str)
    def _panel_layout_changed(self, _key: str) -> None:
        try:
            self.settings.update_loaded_panels(self.gui.loaded_panel_keys())
        except Exception as error:
            logger.error("Could not save loaded-panel selection: %s", error)
        self._sync_tracking_demand()

    def _sync_tracking_demand(self) -> None:
        panel_loaded = self._tracking_panel_loaded()
        for catalog_id, service in self.tracking_services.items():
            should_run = (
                panel_loaded and catalog_id in self.state.enabled_satellite_ids
            )
            if should_run:
                self._start_tracking_service(catalog_id, service)
            else:
                self._stop_tracking_service(catalog_id, service)
        self._update_tracking_timer_and_state()

    def _tracking_panel_loaded(self) -> bool:
        return any(
            self.gui.panel_starts_tracking(key)
            for key in self.gui.loaded_panel_keys()
        )

    def _start_tracking_service(
        self, catalog_id: int, service: SatelliteTrackingService
    ) -> None:
        if not service.running:
            service.start()
        if catalog_id not in self.state.running_satellite_ids:
            self.state.running_satellite_ids.add(catalog_id)
            self.bus.satellite_tracking_state_changed.emit(catalog_id, True)

    def _stop_tracking_service(
        self, catalog_id: int, service: SatelliteTrackingService
    ) -> None:
        if service.running:
            service.stop()
        if catalog_id in self.state.running_satellite_ids:
            self.state.running_satellite_ids.discard(catalog_id)
            self.bus.satellite_tracking_state_changed.emit(catalog_id, False)

    def _update_tracking_timer_and_state(self) -> None:
        running = bool(self.state.running_satellite_ids)
        if running:
            self.tracking_timer.start()
        else:
            self.tracking_timer.stop()
        if running != self.state.tracking_running:
            self.state.tracking_running = running
            self.bus.tracking_running_changed.emit(running)

    @Slot()
    def _deliver_receiver_snapshot(self) -> None:
        snapshot = self.receiver.take_latest()
        if snapshot is not None:
            self.state.receiver_snapshot = snapshot
            self.bus.receiver_snapshot.emit(snapshot)
        self._deliver_receiver_status()
        take_device_update = getattr(self.receiver, "take_device_update", None)
        if take_device_update is not None:
            update = take_device_update()
            if update is not None:
                devices, error = update
                self.state.receiver_devices = devices
                self.bus.receiver_devices_updated.emit(devices)
                if error:
                    logger.warning("HackRF discovery: %s", error)
                else:
                    logger.info("HackRF discovery found %d device(s)", len(devices) - 2)

    def _deliver_receiver_status(self, *, force: bool = False) -> None:
        status = getattr(self.receiver, "status", None)
        if status is None or (not force and status == self._last_receiver_status):
            return
        self._last_receiver_status = status
        self.state.receiver_status = status
        self.bus.receiver_status_changed.emit(status)
        if status.active != self.state.receiver_running:
            self.state.receiver_running = status.active
            self.bus.receiver_running_changed.emit(status.active)
        if status.state == "error":
            logger.error("Receiver error: %s", status.message)

    @Slot()
    def _deliver_orbit_snapshot(self) -> None:
        for catalog_id, service in self.tracking_services.items():
            snapshot = service.take_latest()
            if snapshot is not None:
                self._reported_tracking_errors.pop(catalog_id, None)
                self.state.tracking_errors.pop(catalog_id, None)
                self.state.orbit_snapshots[catalog_id] = snapshot
                self.bus.orbit_snapshot.emit(snapshot)
                continue
            error = service.last_error
            if error is not None and error != self._reported_tracking_errors.get(
                catalog_id
            ):
                self._reported_tracking_errors[catalog_id] = error
                self.state.tracking_errors[catalog_id] = error
                self.bus.tracking_error.emit(catalog_id, error)

    @Slot()
    def shutdown(self) -> None:
        logger.info("Application shutdown requested")
        self.receiver_timer.stop()
        self.tracking_timer.stop()
        if self.receiver.running:
            self.receiver.stop()
        for catalog_id, service in self.tracking_services.items():
            self._stop_tracking_service(catalog_id, service)
        try:
            self.settings.update_window_size(self.gui.width(), self.gui.height())
            self.settings.update_loaded_panels(self.gui.loaded_panel_keys())
        except Exception as error:
            logger.error("Could not save interface settings during shutdown: %s", error)


def make_tracking_services(
    settings: SettingsStore,
) -> dict[int, SatelliteTrackingService]:
    satellite_settings = settings.satellite_settings()
    provider = CelestrakTLEProvider(
        settings,
        refresh_interval=timedelta(
            hours=float(satellite_settings["tle_refresh_hours"])
        ),
        minimum_request_interval=timedelta(
            hours=float(satellite_settings["tle_minimum_request_hours"])
        ),
    )
    return {
        satellite.norad_catalog_id: SatelliteTrackingService(
            provider,
            satellite,
            update_interval=float(satellite_settings["position_update_seconds"]),
            track_refresh_interval=float(
                satellite_settings["ground_track_refresh_seconds"]
            ),
        )
        for satellite in TRACKED_SATELLITES
    }


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationDisplayName(APPLICATION_NAME)
    app.setDesktopFileName(APPLICATION_DESKTOP_ID)
    settings = SettingsStore()
    configure_application_style(app, settings.appearance_mode())
    state = ApplicationState(
        receiver_settings=settings.receiver_settings(),
        receiver_location=settings.receiver_location(),
        enabled_satellite_ids=settings.enabled_satellite_ids(),
        log_verbosity=settings.log_verbosity(),
        appearance_mode=settings.appearance_mode(),
    )
    receiver = ReceiverManager()
    receiver.update_settings(state.receiver_settings)
    state.receiver_status = receiver.status
    bus = AppEventBus()
    gui = MainGUI(PanelContext(bus, state), window_size=settings.window_size())
    log_controller = ApplicationLogController(gui, settings)

    def log_unhandled_exception(exception_type, exception, traceback) -> None:
        logger.critical(
            "Unhandled application error: %s",
            exception,
            exc_info=(exception_type, exception, traceback),
        )

    sys.excepthook = log_unhandled_exception
    orchestrator = AppOrchestrator(
        gui,
        bus,
        state,
        settings,
        receiver,
        make_tracking_services(settings),
    )
    app.aboutToQuit.connect(orchestrator.shutdown)
    app.aboutToQuit.connect(log_controller.shutdown)
    logger.info("Meteor-M LRPT Station starting")
    logger.info("Optional panels are imported only when loaded")
    gui.show()
    gui.load_initial_panels(settings.loaded_panels())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
