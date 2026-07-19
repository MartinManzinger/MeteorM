"""Shared panel contracts and the dynamic Qt panel shell."""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


logger = logging.getLogger("meteor_m.gui")

FloatArray = NDArray[np.float64]
ComplexArray = NDArray[np.complex64]
ImageArray = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class ReceiverSettings:
    center_frequency_hz: float = 137.9e6
    sample_rate_hz: float = 2.0e6
    filter_bandwidth_hz: float = 1.75e6
    lna_gain_db: int = 24
    vga_gain_db: int = 28
    amplifier_enabled: bool = False
    device: str = "Simulated HackRF One"


@dataclass(frozen=True, slots=True)
class ReceiverLocation:
    latitude_deg: float = 52.52
    longitude_deg: float = 13.405


@dataclass(frozen=True, slots=True)
class SpectrumFrame:
    frequency_hz: FloatArray
    power_db: FloatArray


@dataclass(frozen=True, slots=True)
class ReceiverSnapshot:
    created_at: datetime
    spectrum: SpectrumFrame
    constellation: ComplexArray
    image_rgb: ImageArray | None
    image_lines: int
    signal_level_dbfs: float
    symbol_sync: bool
    frame_sync: bool
    packets_decoded: int


@dataclass(frozen=True, slots=True)
class SatelliteDefinition:
    display_name: str
    norad_catalog_id: int
    short_name: str
    lrpt_frequency_hz: float
    map_color: str
    information_url: str
    summary: str

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if self.norad_catalog_id < 1:
            raise ValueError("norad_catalog_id must be positive")
        if self.lrpt_frequency_hz <= 0:
            raise ValueError("lrpt_frequency_hz must be positive")


METEOR_M_N2_3 = SatelliteDefinition(
    "METEOR-M N2-3",
    57166,
    "M2-3",
    137_900_000.0,
    "#55d7ff",
    "https://space.oscar.wmo.int/satellites/view/meteor_m_n2_3",
    "Operational meteorology satellite launched 27 June 2023 into an "
    "approximately 820 km sun-synchronous orbit.",
)
METEOR_M_N2_4 = SatelliteDefinition(
    "METEOR-M N2-4",
    59051,
    "M2-4",
    137_900_000.0,
    "#ff6fb5",
    "https://space.oscar.wmo.int/satellites/view/meteor_m_n2_4",
    "Operational meteorology satellite launched 29 February 2024 into an "
    "approximately 821 km sun-synchronous orbit.",
)

METEOR_SATELLITES = (METEOR_M_N2_3, METEOR_M_N2_4)


@dataclass(frozen=True, slots=True)
class TLERecord:
    satellite: SatelliteDefinition
    name: str
    line1: str
    line2: str
    retrieved_at: datetime
    source_url: str
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class SatellitePosition:
    observed_at: datetime
    latitude_deg: float
    longitude_deg: float
    altitude_km: float


@dataclass(frozen=True, slots=True)
class OrbitSnapshot:
    satellite: SatelliteDefinition
    position: SatellitePosition
    ground_track: tuple[SatellitePosition, ...]
    tle_epoch: datetime
    tle_retrieved_at: datetime
    using_cached_tle: bool


class AppEventBus(QObject):
    receiver_start_requested = Signal()
    receiver_stop_requested = Signal()
    receiver_settings_requested = Signal(object)
    location_requested = Signal(object)
    tle_refresh_requested = Signal(int)
    satellite_enabled_requested = Signal(int, bool)
    appearance_requested = Signal(str)

    receiver_snapshot = Signal(object)
    orbit_snapshot = Signal(object)
    orbit_removed = Signal(int)
    receiver_running_changed = Signal(bool)
    tracking_running_changed = Signal(bool)
    satellite_tracking_state_changed = Signal(int, bool)
    satellite_enabled_changed = Signal(int, bool)
    appearance_changed = Signal(str)
    receiver_settings_updated = Signal(object)
    location_updated = Signal(object)
    tracking_error = Signal(int, str)


@dataclass(slots=True)
class ApplicationState:
    receiver_settings: ReceiverSettings
    receiver_location: ReceiverLocation
    receiver_running: bool = False
    tracking_running: bool = False
    receiver_snapshot: ReceiverSnapshot | None = None
    orbit_snapshots: dict[int, OrbitSnapshot] = field(default_factory=dict)
    tracking_errors: dict[int, str] = field(default_factory=dict)
    enabled_satellite_ids: set[int] = field(
        default_factory=lambda: {
            METEOR_M_N2_3.norad_catalog_id,
            METEOR_M_N2_4.norad_catalog_id,
        }
    )
    running_satellite_ids: set[int] = field(default_factory=set)
    log_verbosity: str = "INFO"
    appearance_mode: str = "dark"


@dataclass(frozen=True, slots=True)
class PanelContext:
    bus: AppEventBus
    state: ApplicationState


@dataclass(frozen=True, slots=True)
class PanelSpec:
    key: str
    title: str
    module: str
    description: str
    row: int
    column: int
    row_span: int
    column_span: int
    starts_tracking: bool = False


PANEL_SPECS = (
    PanelSpec(
        "location",
        "Receiver location",
        "panel_location",
        "Manual coordinates, Google Maps paste, and map-position synchronization.",
        0,
        0,
        4,
        1,
    ),
    PanelSpec(
        "satellite_tracking",
        "Satellite tracking",
        "panel_satellite_tracking",
        "TLE cache, manual online refresh, actual position, and projected track.",
        0,
        0,
        4,
        1,
        starts_tracking=True,
    ),
    PanelSpec(
        "sdr",
        "SDR control",
        "panel_sdr",
        "HackRF-style frequency, sample-rate, bandwidth, gain, and lifecycle controls.",
        4,
        0,
        4,
        2,
    ),
    PanelSpec(
        "spectrum",
        "Live spectrum",
        "panel_spectrum",
        "FFT spectral plot with a bounded scrolling waterfall.",
        0,
        3,
        4,
        2,
    ),
    PanelSpec(
        "constellation",
        "IQ constellation",
        "panel_constellation",
        "Bounded decoded I/Q symbol point cloud.",
        0,
        5,
        4,
        1,
    ),
    PanelSpec(
        "picture",
        "Decoded picture",
        "panel_picture",
        "Incrementally reconstructed LRPT image display.",
        4,
        4,
        4,
        2,
    ),
)


MANDATORY_PANEL_SPECS = (
    PanelSpec(
        "map",
        "World map",
        "panel_map",
        "Receiver location and independently colored satellite ground tracks.",
        0,
        1,
        4,
        2,
    ),
    PanelSpec(
        "log",
        "Application log",
        "panel_log",
        "Application activity, warnings, and errors.",
        4,
        2,
        4,
        2,
    ),
)


LEGACY_PANEL_KEYS = {
    "receiver": "sdr",
    "satellite": "satellite_tracking",
    "image": "picture",
    "map": None,
    "status": None,
}


STYLESHEET = """
QWidget {
    background: #0b1119; color: #d8e2ec;
    font-family: "Inter", "Noto Sans", sans-serif; font-size: 12px;
}
QFrame#panelSlot {
    background: #0f1722; border: 1px solid #243246; border-radius: 6px;
}
QFrame#panelSlot[loaded="false"] {
    background: #0b121b; border: 1px dashed #30425a;
}
QFrame#panelSlotHeader {
    background: #111925; border: 1px solid #243246; border-radius: 7px;
}
QLabel#panelSlotTitle {
    color: #a9bbcd; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}
QLabel#dimLabel { color: #778ba2; }
QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #192434; border: 1px solid #30425a; border-radius: 4px; padding: 6px;
}
QPushButton:hover { border-color: #55d7ff; color: #ffffff; }
QPushButton:pressed { background: #22344b; }
QPushButton#primaryButton {
    background: #176c87; border-color: #55d7ff; color: white; font-weight: 700;
}
QPushButton#panelPlaceholder {
    background: transparent; border: none; color: #70849a; font-weight: 600;
}
QPushButton#panelPlaceholder:hover {
    background: #111d2a; color: #55d7ff;
}
QToolButton#panelCloseButton {
    background: transparent; border: none; color: #778ba2; font-size: 15px;
    padding: 0 5px;
}
QToolButton#panelCloseButton:hover { color: #ff9d83; }
QToolButton#panelChoiceButton {
    background: transparent; border: none; color: #778ba2; font-size: 11px;
    font-weight: 700; padding: 3px 6px;
}
QToolButton#panelChoiceButton:hover { color: #ffffff; }
QToolButton#panelChoiceButton:checked {
    background: #176c87; border-radius: 3px; color: #ffffff;
}
QCheckBox { spacing: 7px; }
QPlainTextEdit {
    background: #0c131c; border: 1px solid #293a50; border-radius: 4px;
    color: #b9c8d6; font-family: "Noto Sans Mono", monospace; font-size: 11px;
}
QSplitter::handle { background: #172233; width: 5px; height: 5px; }
"""



class PanelSlot(QFrame):
    load_requested = Signal(str)
    unload_requested = Signal(str)

    def __init__(
        self,
        spec: PanelSpec,
        *,
        mandatory: bool = False,
        alternate_specs: tuple[PanelSpec, ...] = (),
    ) -> None:
        super().__init__()
        self.spec = spec
        self.specs = {item.key: item for item in (spec, *alternate_specs)}
        self.mandatory = mandatory
        self._panel: QWidget | None = None
        self.choice_buttons: dict[str, QToolButton] = {}
        self.setObjectName("panelSlot")
        self.setProperty("loaded", mandatory)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumSize(145 * spec.column_span, 68 * spec.row_span)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(3)
        header = QFrame()
        header.setObjectName("panelSlotHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(7, 3, 3, 3)
        self.title_label = QLabel(spec.title.upper())
        self.title_label.setObjectName("panelSlotTitle")
        header_layout.addWidget(self.title_label)
        if alternate_specs:
            self.title_label.hide()
            for choice_spec in (spec, *alternate_specs):
                button = QToolButton()
                button.setObjectName("panelChoiceButton")
                button.setText(
                    "SATELLITE"
                    if choice_spec.key == "satellite_tracking"
                    else "RECEIVER"
                )
                button.setCheckable(True)
                button.setAutoExclusive(True)
                button.setToolTip(f"Load {choice_spec.title}")
                button.clicked.connect(
                    lambda checked=False, key=choice_spec.key: (
                        self.load_requested.emit(key)
                    )
                )
                self.choice_buttons[choice_spec.key] = button
                header_layout.addWidget(button)
        header_layout.addStretch()
        self.close_button = QToolButton()
        self.close_button.setObjectName("panelCloseButton")
        self.close_button.setText("×")
        self.close_button.setToolTip(f"Unload {spec.title}")
        self.close_button.setVisible(not mandatory)
        self.close_button.clicked.connect(
            lambda checked=False: self.unload_requested.emit(self.spec.key)
        )
        header_layout.addWidget(self.close_button)
        outer.addWidget(header)

        content = QWidget()
        self.stack = QStackedLayout(content)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QPushButton()
        self.placeholder.setObjectName("panelPlaceholder")
        self.placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.placeholder.clicked.connect(
            lambda checked=False: self.load_requested.emit(self.spec.key)
        )
        self.stack.addWidget(self.placeholder)
        outer.addWidget(content, 1)
        self.show_inactive(spec=spec)

    def activate_spec(self, spec: PanelSpec) -> None:
        if spec.key not in self.specs:
            raise KeyError(f"{spec.key} does not belong to this panel slot")
        self.spec = spec
        self.title_label.setText(spec.title.upper())
        self.close_button.setToolTip(f"Unload {spec.title}")
        for key, button in self.choice_buttons.items():
            button.setChecked(key == spec.key)

    def show_loading(self, spec: PanelSpec | None = None) -> None:
        if spec is not None:
            self.activate_spec(spec)
        self.placeholder.setEnabled(False)
        self.placeholder.setText(f"{self.spec.title}\n\nLoading…")
        self.stack.setCurrentWidget(self.placeholder)

    def show_inactive(
        self, error: str | None = None, spec: PanelSpec | None = None
    ) -> None:
        if spec is not None:
            self.activate_spec(spec)
        self.placeholder.setEnabled(True)
        self.close_button.setEnabled(False)
        if error:
            self.placeholder.setText(
                f"{self.spec.title}\n\nUnavailable: {error}\nClick to retry"
            )
        else:
            self.placeholder.setText(
                f"{self.spec.title}\n\nNot loaded · click to load"
            )
        self.stack.setCurrentWidget(self.placeholder)
        self.setProperty("loaded", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_panel(self, panel: QWidget, spec: PanelSpec | None = None) -> None:
        if spec is not None:
            self.activate_spec(spec)
        self._panel = panel
        self.stack.addWidget(panel)
        self.stack.setCurrentWidget(panel)
        self.close_button.setEnabled(True)
        self.setProperty("loaded", True)
        self.style().unpolish(self)
        self.style().polish(self)

    def take_panel(self) -> QWidget | None:
        panel = self._panel
        self._panel = None
        self.close_button.setEnabled(False)
        if panel is not None:
            self.stack.removeWidget(panel)
        self.show_inactive()
        return panel

    @property
    def panel(self) -> QWidget | None:
        return self._panel


class MainGUI(QMainWindow):
    panel_loaded = Signal(str)
    panel_unloaded = Signal(str)

    def __init__(
        self,
        context: PanelContext,
        *,
        log_verbosity: str | None = None,
        window_size: tuple[int, int] = (1480, 920),
        panel_specs: tuple[PanelSpec, ...] = PANEL_SPECS,
    ) -> None:
        super().__init__()
        self.context = context
        if log_verbosity is not None:
            context.state.log_verbosity = log_verbosity
        self._specs = {spec.key: spec for spec in panel_specs}
        self._mandatory_specs = {
            spec.key: spec for spec in MANDATORY_PANEL_SPECS
        }
        self._slots: dict[str, PanelSlot] = {}
        self._panels: dict[str, QWidget] = {}
        self._appearance_actions: dict[str, QAction] = {}
        self.setWindowTitle("Meteor-M LRPT Station")
        self.resize(*window_size)
        self.setMinimumSize(1050, 700)
        self._build_menu()
        self._build_panel_grid()
        self.map_panel = self._panels["map"]
        self.log_panel = self._panels["log"]
        context.bus.appearance_changed.connect(self._show_appearance)
        self._show_appearance(context.state.appearance_mode)

    def load_initial_panels(self, keys: tuple[str, ...]) -> None:
        loaded: set[str] = set()
        for key in keys:
            current_key = LEGACY_PANEL_KEYS.get(key, key)
            if current_key is None or current_key in loaded:
                continue
            loaded.add(current_key)
            self.load_panel(current_key)

    def load_panel(self, key: str) -> bool:
        if key in self._panels:
            slot = self._slots.get(key)
            spec = self._specs.get(key) or self._mandatory_specs.get(key)
            if slot is not None and spec is not None:
                slot.activate_spec(spec)
            self._panels[key].setFocus()
            return True
        spec = self._specs.get(key)
        if spec is None:
            logger.error("Cannot load unknown panel %r", key)
            return False
        slot = self._slots[key]
        occupying_key = next(
            (
                candidate
                for candidate, candidate_slot in self._slots.items()
                if candidate_slot is slot and candidate in self._panels
            ),
            None,
        )
        if occupying_key is not None and occupying_key != key:
            self.unload_panel(occupying_key)
        slot.show_loading(spec)
        try:
            panel = self._construct_panel(spec.module)
        except ModuleNotFoundError as error:
            message = f"missing module or dependency: {error.name}"
            logger.error("Could not load %s panel; %s", spec.title, message)
            logger.debug("Panel import traceback", exc_info=True)
            slot.show_inactive(message, spec)
            return False
        except Exception as error:
            logger.error("Could not load %s panel: %s", spec.title, error)
            logger.debug("Panel construction traceback", exc_info=True)
            slot.show_inactive(str(error), spec)
            return False

        self._panels[key] = panel
        slot.set_panel(panel, spec)
        logger.info("Loaded panel: %s (%s)", spec.title, spec.module)
        self.panel_loaded.emit(key)
        return True

    def unload_panel(self, key: str) -> None:
        if key in self._mandatory_specs:
            return
        panel = self._panels.pop(key, None)
        if panel is None:
            return
        self._slots[key].take_panel()
        panel.setParent(None)
        panel.deleteLater()
        logger.info("Unloaded panel: %s", self._specs[key].title)
        self.panel_unloaded.emit(key)

    def loaded_panel_keys(self) -> tuple[str, ...]:
        return tuple(
            spec.key for spec in self._specs.values() if spec.key in self._panels
        )

    def panel_instance(self, key: str) -> QWidget | None:
        return self._panels.get(key)

    def panel_slot(self, key: str) -> PanelSlot | None:
        return self._slots.get(key)

    def panel_layout_position(self, key: str) -> tuple[int, int, int, int] | None:
        spec = self._specs.get(key) or self._mandatory_specs.get(key)
        if spec is None:
            return None
        return spec.row, spec.column, spec.row_span, spec.column_span

    def panel_starts_tracking(self, key: str) -> bool:
        spec = self._specs.get(key)
        return bool(spec and spec.starts_tracking)

    def _construct_panel(self, module_name: str) -> QWidget:
        module = importlib.import_module(module_name)
        panel_type = getattr(module, "Panel")
        panel = panel_type(self.context)
        if not isinstance(panel, QWidget):
            raise TypeError(f"{module_name}.Panel did not create a QWidget")
        return panel

    def _build_panel_grid(self) -> None:
        central = QWidget()
        grid = QGridLayout(central)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for column in range(6):
            grid.setColumnStretch(column, 1)
        for row in range(8):
            grid.setRowStretch(row, 1)

        for spec in MANDATORY_PANEL_SPECS:
            slot = PanelSlot(spec, mandatory=True)
            slot.load_requested.connect(self.load_panel)
            slot.unload_requested.connect(self.unload_panel)
            self._slots[spec.key] = slot
            grid.addWidget(
                slot,
                spec.row,
                spec.column,
                spec.row_span,
                spec.column_span,
            )
            panel = self._construct_panel(spec.module)
            self._panels[spec.key] = panel
            slot.set_panel(panel)

        shared_keys: set[str] = set()
        if {"satellite_tracking", "location"} <= self._specs.keys():
            tracking_spec = self._specs["satellite_tracking"]
            location_spec = self._specs["location"]
            slot = PanelSlot(
                tracking_spec,
                alternate_specs=(location_spec,),
            )
            slot.load_requested.connect(self.load_panel)
            slot.unload_requested.connect(self.unload_panel)
            self._slots[tracking_spec.key] = slot
            self._slots[location_spec.key] = slot
            grid.addWidget(
                slot,
                tracking_spec.row,
                tracking_spec.column,
                tracking_spec.row_span,
                tracking_spec.column_span,
            )
            shared_keys.update((tracking_spec.key, location_spec.key))

        for spec in self._specs.values():
            if spec.key in shared_keys:
                continue
            slot = PanelSlot(spec)
            slot.load_requested.connect(self.load_panel)
            slot.unload_requested.connect(self.unload_panel)
            self._slots[spec.key] = slot
            grid.addWidget(
                slot,
                spec.row,
                spec.column,
                spec.row_span,
                spec.column_span,
            )
        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        settings_menu = self.menuBar().addMenu("&Settings")
        appearance_menu = settings_menu.addMenu("Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label in (("dark", "Dark mode"), ("normal", "Normal mode")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(mode)
            action.triggered.connect(
                lambda checked=False, selected=mode: (
                    self.context.bus.appearance_requested.emit(selected)
                    if checked
                    else None
                )
            )
            group.addAction(action)
            appearance_menu.addAction(action)
            self._appearance_actions[mode] = action

    def _show_appearance(self, mode: str) -> None:
        normalized = mode if mode in self._appearance_actions else "dark"
        self._appearance_actions[normalized].setChecked(True)


def configure_application_style(
    app: QApplication, appearance_mode: str = "dark"
) -> None:
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet(STYLESHEET if appearance_mode == "dark" else "")
