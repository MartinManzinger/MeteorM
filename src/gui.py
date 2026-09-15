"""Shared panel contracts and the dynamic Qt panel shell."""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPoint, QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


logger = logging.getLogger("meteor_m.gui")

APPLICATION_ICON_PATH = (
    Path(__file__).resolve().parent.parent / "info" / "master_icon.png"
)
APPLICATION_NAME = "Meteor-M LRPT Station"
APPLICATION_DESKTOP_ID = "MeteorM"
APPEARANCE_MODES = ("green", "dark", "normal")
SIMULATION_DEVICE = "Simulated HackRF One"
HACKRF_AUTO_DEVICE = "HackRF One (auto)"
RECEIVER_SAMPLE_RATE_MIN_HZ = 2_000_000.0
RECEIVER_SAMPLE_RATE_MAX_HZ = 20_000_000.0
RECEIVER_SAMPLE_RATE_STEP_HZ = 1_000_000.0


def normalize_appearance_mode(appearance_mode: str) -> str:
    normalized = appearance_mode.lower()
    return normalized if normalized in APPEARANCE_MODES else "green"

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
    device: str = SIMULATION_DEVICE


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
class ReceiverStatus:
    state: str = "stopped"
    backend: str = "Simulation"
    device: str = SIMULATION_DEVICE
    message: str = "Receiver stopped"
    samples_received: int = 0
    dropped_buffers: int = 0

    @property
    def active(self) -> bool:
        return self.state in {"discovering", "starting", "running"}


@dataclass(frozen=True, slots=True)
class SatelliteDefinition:
    display_name: str
    norad_catalog_id: int
    short_name: str
    receiver_frequency_hz: float | None
    receiver_mode: str | None
    map_color: str
    information_url: str
    summary: str

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if self.norad_catalog_id < 1:
            raise ValueError("norad_catalog_id must be positive")
        if (
            self.receiver_frequency_hz is not None
            and self.receiver_frequency_hz <= 0
        ):
            raise ValueError("receiver_frequency_hz must be positive when provided")
        if (self.receiver_frequency_hz is None) != (self.receiver_mode is None):
            raise ValueError("receiver frequency and mode must be provided together")


METEOR_M_N2_3 = SatelliteDefinition(
    "METEOR-M N2-3",
    57166,
    "M2-3",
    137_900_000.0,
    "LRPT",
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
    "LRPT",
    "#ff6fb5",
    "https://space.oscar.wmo.int/satellites/view/meteor_m_n2_4",
    "Operational meteorology satellite launched 29 February 2024 into an "
    "approximately 821 km sun-synchronous orbit.",
)

METEOR_SATELLITES = (METEOR_M_N2_3, METEOR_M_N2_4)

NOAA_15 = SatelliteDefinition(
    "NOAA-15",
    25338,
    "NOAA-15",
    137_620_000.0,
    "APT",
    "#ffd166",
    "https://space.oscar.wmo.int/satellites/view/noaa_15",
    "Legacy NOAA polar-orbiting weather satellite launched 13 May 1998; "
    "included for tracking and manual APT tuning.",
)
NOAA_18 = SatelliteDefinition(
    "NOAA-18",
    28654,
    "NOAA-18",
    137_912_500.0,
    "APT",
    "#a98bff",
    "https://space.oscar.wmo.int/satellites/view/noaa_18",
    "Legacy NOAA polar-orbiting weather satellite launched 20 May 2005; "
    "included for tracking and manual APT tuning.",
)
NOAA_19 = SatelliteDefinition(
    "NOAA-19",
    33591,
    "NOAA-19",
    137_100_000.0,
    "APT",
    "#ff8a65",
    "https://space.oscar.wmo.int/satellites/view/noaa_19",
    "Legacy NOAA polar-orbiting weather satellite launched 6 February 2009; "
    "included for tracking and manual APT tuning.",
)

NOAA_SATELLITES = (NOAA_15, NOAA_18, NOAA_19)
TRACKED_SATELLITES = (*METEOR_SATELLITES, *NOAA_SATELLITES)


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
    receiver_devices_requested = Signal()
    location_requested = Signal(object)
    tle_refresh_requested = Signal(int)
    satellite_enabled_requested = Signal(int, bool)
    appearance_requested = Signal(str)

    receiver_snapshot = Signal(object)
    orbit_snapshot = Signal(object)
    orbit_removed = Signal(int)
    receiver_running_changed = Signal(bool)
    receiver_status_changed = Signal(object)
    receiver_devices_updated = Signal(object)
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
    receiver_status: ReceiverStatus = field(default_factory=ReceiverStatus)
    receiver_devices: tuple[str, ...] = (
        SIMULATION_DEVICE,
        HACKRF_AUTO_DEVICE,
    )
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
    appearance_mode: str = "green"


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
        "Simulation or receive-only HackRF discovery, RF settings, and status.",
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
        "Bounded raw/simulated I/Q point cloud pending LRPT demodulation.",
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


DARK_STYLESHEET = """
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
QFrame#windowTitleBar {
    background: #111925; border: 1px solid #243246; border-radius: 5px;
}
QLabel#windowTitle { color: #d8e2ec; font-size: 12px; font-weight: 600; }
QToolButton#titleMenuButton, QToolButton#windowControlButton,
QToolButton#windowCloseButton {
    background: transparent; border: none; border-radius: 3px; padding: 4px 9px;
}
QToolButton#titleMenuButton:hover, QToolButton#windowControlButton:hover {
    background: #22344b; color: #ffffff;
}
QToolButton#windowCloseButton:hover { background: #b83b45; color: #ffffff; }
QLabel#panelSlotTitle {
    color: #a9bbcd; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}
QLabel#dimLabel { color: #778ba2; }
QMenu {
    background: #111925; color: #d8e2ec; border: 1px solid #30425a;
    padding: 4px;
}
QMenu::item {
    background: transparent; border-radius: 3px; padding: 6px 28px 6px 10px;
}
QMenu::item:selected { background: #22344b; color: #ffffff; }
QMenu::separator { background: #30425a; height: 1px; margin: 4px 7px; }
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
QToolButton#satelliteAccordionHeader {
    background: #192434; border: 1px solid #30425a; border-radius: 4px;
    color: #d8e2ec; font-weight: 700; padding: 7px; text-align: left;
}
QToolButton#satelliteAccordionHeader:hover {
    background: #22344b; border-color: #55d7ff;
}
QCheckBox { spacing: 7px; }
QPlainTextEdit {
    background: #0c131c; border: 1px solid #293a50; border-radius: 4px;
    color: #b9c8d6; font-family: "Noto Sans Mono", monospace; font-size: 11px;
}
QSplitter::handle { background: #172233; width: 5px; height: 5px; }
"""

GREEN_STYLESHEET = DARK_STYLESHEET + """
QFrame#windowTitleBar, QFrame#panelSlotHeader {
    background: #102019; border-color: #28513c;
}
QFrame#panelSlot { background: #0d1712; border-color: #244735; }
QFrame#panelSlot[loaded="false"] {
    background: #0a130f; border-color: #315a43;
}
QLabel#panelSlotTitle, QLabel#windowTitle { color: #c9e2d1; }
QLabel#dimLabel { color: #7fa18a; }
QMenu { background: #102019; color: #c9e2d1; border-color: #365e46; }
QMenu::item:selected { background: #27784a; color: #ffffff; }
QMenu::separator { background: #365e46; }
QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #16271d; border-color: #365e46;
}
QPushButton:hover { border-color: #71dc99; color: #f5fff8; }
QPushButton:pressed { background: #203c2b; }
QPushButton#primaryButton, QToolButton#panelChoiceButton:checked {
    background: #27784a; border-color: #71dc99; color: #ffffff;
}
QPushButton#panelPlaceholder { color: #78a98a; }
QPushButton#panelPlaceholder:hover {
    background: #13251a; color: #71dc99;
}
QToolButton#titleMenuButton:hover, QToolButton#windowControlButton:hover {
    background: #203c2b; color: #ffffff;
}
QToolButton#satelliteAccordionHeader {
    background: #16271d; border-color: #365e46; color: #c9e2d1;
}
QToolButton#satelliteAccordionHeader:hover {
    background: #203c2b; border-color: #71dc99;
}
QPlainTextEdit {
    background: #09120d; border-color: #294c37; color: #bed3c4;
}
QSplitter::handle { background: #193124; }
"""


class ResizeHandle(QWidget):
    def __init__(self, edges, cursor: Qt.CursorShape) -> None:
        super().__init__()
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self.edges)
            event.accept()
            return
        super().mousePressEvent(event)


class WindowChrome(QWidget):
    """Frameless-window content surrounded by native system-resize handles."""

    MARGIN = 5

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        margin = self.MARGIN
        handles = (
            (
                0,
                0,
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
            (0, 1, Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            (
                0,
                2,
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            (1, 0, Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            (1, 2, Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (
                2,
                0,
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            (2, 1, Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (
                2,
                2,
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
        )
        for row, column, edges, cursor in handles:
            handle = ResizeHandle(edges, cursor)
            if row in (0, 2):
                handle.setFixedHeight(margin)
            if column in (0, 2):
                handle.setFixedWidth(margin)
            layout.addWidget(handle, row, column)
        layout.addWidget(content, 1, 1)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(1, 1)


class WindowTitleBar(QFrame):
    def __init__(self, window: QMainWindow, icon_path: Path) -> None:
        super().__init__()
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(3)
        side_width = 120
        self.left_controls = QWidget()
        self.left_controls.setFixedWidth(side_width)
        left_layout = QHBoxLayout(self.left_controls)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(3)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("windowIcon")
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            self.icon_label.setPixmap(
                pixmap.scaled(
                    22,
                    22,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        left_layout.addWidget(self.icon_label)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("titleMenuButton")
        self.settings_button.setText("Settings")
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        left_layout.addWidget(self.settings_button)
        left_layout.addStretch()
        layout.addWidget(self.left_controls)
        layout.addStretch(1)

        self.title_label = QLabel(window.windowTitle())
        self.title_label.setObjectName("windowTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.right_controls = QWidget()
        self.right_controls.setFixedWidth(side_width)
        right_layout = QHBoxLayout(self.right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(3)
        self.minimize_button = self._control_button("−", "Minimize")
        self.maximize_button = self._control_button("□", "Maximize")
        self.close_button = self._control_button("×", "Close", close=True)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_maximized)
        self.close_button.clicked.connect(window.close)
        right_layout.addWidget(self.minimize_button)
        right_layout.addWidget(self.maximize_button)
        right_layout.addWidget(self.close_button)
        layout.addWidget(self.right_controls)

    @staticmethod
    def _control_button(
        text: str, tooltip: str, *, close: bool = False
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("windowCloseButton" if close else "windowControlButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(38, 28)
        return button

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
            self.maximize_button.setText("□")
            self.maximize_button.setToolTip("Maximize")
        else:
            self._window.showMaximized()
            self.maximize_button.setText("❐")
            self.maximize_button.setToolTip("Restore")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._drag_offset = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._window.isMaximized()
        ):
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


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
        self.setWindowTitle(APPLICATION_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        application_icon = QIcon(str(APPLICATION_ICON_PATH))
        self.setWindowIcon(application_icon)
        application = QApplication.instance()
        if application is not None:
            application.setApplicationName(APPLICATION_NAME)
            application.setApplicationDisplayName(APPLICATION_NAME)
            application.setDesktopFileName(APPLICATION_DESKTOP_ID)
            application.setWindowIcon(application_icon)
        self.resize(*window_size)
        self.setMinimumSize(1050, 700)
        self._build_title_bar()
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
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        content_layout.addWidget(self.title_bar)
        content_layout.addWidget(central, 1)
        self.setCentralWidget(WindowChrome(content))

    def _build_title_bar(self) -> None:
        self.title_bar = WindowTitleBar(self, APPLICATION_ICON_PATH)
        self.settings_menu = QMenu(self.title_bar)
        self.settings_menu.setTitle("Settings")
        appearance_menu = self.settings_menu.addMenu("Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label in (
            ("green", "Green mode"),
            ("dark", "Dark mode"),
            ("normal", "Normal mode"),
        ):
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
        self.title_bar.settings_button.setMenu(self.settings_menu)

    def _show_appearance(self, mode: str) -> None:
        normalized = normalize_appearance_mode(mode)
        self._appearance_actions[normalized].setChecked(True)


def configure_application_style(
    app: QApplication, appearance_mode: str = "green"
) -> None:
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    normalized = normalize_appearance_mode(appearance_mode)
    if normalized == "green":
        app.setStyleSheet(GREEN_STYLESHEET)
    elif normalized == "dark":
        app.setStyleSheet(DARK_STYLESHEET)
    else:
        app.setStyleSheet("")
