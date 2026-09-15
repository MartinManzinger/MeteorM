"""Optional controls and status for Meteor-M and NOAA orbit tracking."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import (
    METEOR_SATELLITES,
    NOAA_SATELLITES,
    OrbitSnapshot,
    PanelContext,
    SatelliteDefinition,
)


logger = logging.getLogger("meteor_m.panel.satellite_tracking")


class SatelliteCard(QGroupBox):
    expansion_requested = Signal(bool)

    def __init__(self, satellite: SatelliteDefinition, context: PanelContext) -> None:
        super().__init__()
        self.satellite = satellite
        self.context = context
        catalog_id = satellite.norad_catalog_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.header = QToolButton()
        self.header.setObjectName("satelliteAccordionHeader")
        self.header.setText(satellite.display_name)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.header)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(7, 3, 7, 7)
        details_layout.setSpacing(5)
        layout.addWidget(self.details)

        self.enabled = QCheckBox(f"Track · NORAD {catalog_id}")
        self.enabled.setToolTip("Activate tracking and the colored map overlay")
        self.enabled.setObjectName(f"satelliteEnabled_{catalog_id}")
        self.enabled.setChecked(catalog_id in context.state.enabled_satellite_ids)
        details_layout.addWidget(self.enabled)

        grid = QGridLayout()
        self.worker = QLabel("STOPPED")
        self.position = QLabel("Waiting for orbital elements")
        self.projection = QLabel("--")
        self.source = QLabel("--")
        self.epoch = QLabel("--")
        self.retrieved = QLabel("--")
        receiver_supported = satellite.receiver_frequency_hz is not None
        self.frequency = QLabel(
            f"{satellite.receiver_frequency_hz / 1e6:.4f} MHz"
            if receiver_supported
            else "Trajectory only"
        )
        for row, (name, value) in enumerate(
            (
                ("Worker", self.worker),
                ("Actual position", self.position),
                ("Projected track", self.projection),
                ("TLE source", self.source),
                ("TLE epoch", self.epoch),
                ("Fetched", self.retrieved),
                (
                    f"{satellite.receiver_mode} tune"
                    if receiver_supported
                    else "Receiver support",
                    self.frequency,
                ),
            )
        ):
            label = QLabel(name)
            label.setObjectName("dimLabel")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setWordWrap(True)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
        details_layout.addLayout(grid)

        buttons = QGridLayout()
        self.fetch_button = QPushButton("Fetch TLE")
        self.fetch_button.setObjectName(f"fetchTle_{catalog_id}")
        self.fetch_button.setToolTip(
            "Request this satellite from CelesTrak in the worker thread and store "
            "validated data in settings.yaml. The two-hour limit still applies."
        )
        self.tune_button: QPushButton | None = None
        if satellite.receiver_frequency_hz is not None:
            self.tune_button = QPushButton("Tune SDR")
            self.tune_button.setObjectName(f"tuneSdr_{catalog_id}")
            self.tune_button.setToolTip(
                "Set SDR center frequency to "
                f"{satellite.receiver_frequency_hz / 1e6:.4f} MHz"
            )
        self.info_button = QPushButton("Info…")
        self.info_button.setObjectName(f"satelliteInfo_{catalog_id}")
        buttons.addWidget(self.fetch_button, 0, 0)
        if self.tune_button is not None:
            buttons.addWidget(self.tune_button, 0, 1)
        buttons.addWidget(self.info_button, 1, 0, 1, 2)
        details_layout.addLayout(buttons)

        self.enabled.toggled.connect(self._request_enabled)
        self.fetch_button.clicked.connect(
            lambda checked=False: context.bus.tle_refresh_requested.emit(catalog_id)
        )
        if self.tune_button is not None:
            self.tune_button.clicked.connect(self._tune_receiver)
        self.info_button.clicked.connect(self._show_information)
        self.header.toggled.connect(self.expansion_requested.emit)
        self._set_controls_enabled(self.enabled.isChecked())
        self.set_expanded(False)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        blocker = QSignalBlocker(self.header)
        self.header.setChecked(expanded)
        del blocker
        self.header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.details.setVisible(expanded)

    def set_enabled(self, enabled: bool) -> None:
        blocker = QSignalBlocker(self.enabled)
        self.enabled.setChecked(enabled)
        del blocker
        self._set_controls_enabled(enabled)
        if not enabled:
            self.worker.setText("DISABLED")

    def set_running(self, running: bool) -> None:
        self.worker.setText("ACTIVE" if running else "STOPPED")

    def show_orbit(self, snapshot: OrbitSnapshot) -> None:
        point = snapshot.position
        self.position.setText(
            f"{point.latitude_deg:+.3f}°, {point.longitude_deg:+.3f}° · "
            f"{point.altitude_km:.0f} km"
        )
        track = snapshot.ground_track
        if track:
            start = track[0].observed_at.astimezone().strftime("%H:%M")
            end = track[-1].observed_at.astimezone().strftime("%H:%M")
            self.projection.setText(f"{len(track)} points · {start}–{end}")
        else:
            self.projection.setText("Calculating…")
        self.source.setText(
            "settings.yaml cache" if snapshot.using_cached_tle else "CelesTrak online"
        )
        self.epoch.setText(snapshot.tle_epoch.astimezone().strftime("%Y-%m-%d %H:%M"))
        self.retrieved.setText(
            snapshot.tle_retrieved_at.astimezone().strftime("%Y-%m-%d %H:%M")
        )

    def show_error(self, message: str) -> None:
        self.position.setText("UNAVAILABLE")
        self.projection.setText("See application log")
        self.source.setText(message)

    def _request_enabled(self, enabled: bool) -> None:
        self._set_controls_enabled(enabled)
        self.context.bus.satellite_enabled_requested.emit(
            self.satellite.norad_catalog_id, enabled
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.fetch_button.setEnabled(enabled)

    def _tune_receiver(self) -> None:
        if self.satellite.receiver_frequency_hz is None:
            return
        settings = replace(
            self.context.state.receiver_settings,
            center_frequency_hz=self.satellite.receiver_frequency_hz,
        )
        self.context.bus.receiver_settings_requested.emit(settings)
        logger.info(
            "Requested SDR tune for %s at %.4f MHz",
            self.satellite.display_name,
            self.satellite.receiver_frequency_hz / 1e6,
        )

    def _show_information(self) -> None:
        message = QMessageBox(self)
        self._information_message = message
        message.setWindowTitle(self.satellite.display_name)
        message.setIcon(QMessageBox.Icon.Information)
        receiver_frequency = self.satellite.receiver_frequency_hz
        receiver_detail = (
            f"Configured {self.satellite.receiver_mode} tune: "
            f"{receiver_frequency / 1e6:.4f} MHz"
            if receiver_frequency is not None
            else "Receiver support: trajectory display only"
        )
        message.setText(
            f"<b>{self.satellite.display_name}</b><br>"
            f"NORAD catalog ID: {self.satellite.norad_catalog_id}<br>"
            f"{receiver_detail}"
        )
        reception_note = (
            "Meteor LRPT can switch among its assigned frequencies; verify current "
            "transmission status when reception fails."
            if self.satellite.receiver_mode == "LRPT"
            else "The SDR can tune this legacy APT frequency, but this application "
            "does not demodulate or decode NOAA transmissions."
        )
        message.setInformativeText(
            f"{self.satellite.summary}\n\n{reception_note}\n\n"
            f"WMO OSCAR: {self.satellite.information_url}"
        )
        message.setStandardButtons(QMessageBox.StandardButton.Close)
        message.open()


class Panel(QScrollArea):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        self.context = context
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        explanation = QLabel(
            "Expand one satellite at a time. Tracking controls its map trajectory; "
            "NOAA APT tuning is available without decoding."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("dimLabel")
        layout.addWidget(explanation)

        all_satellites = (*METEOR_SATELLITES, *NOAA_SATELLITES)
        self.cards = {
            satellite.norad_catalog_id: SatelliteCard(satellite, context)
            for satellite in all_satellites
        }
        for heading, satellites in (
            ("Meteor-M LRPT", METEOR_SATELLITES),
            ("NOAA APT tracking", NOAA_SATELLITES),
        ):
            label = QLabel(heading)
            label.setObjectName("sectionTitle")
            layout.addWidget(label)
            for satellite in satellites:
                layout.addWidget(self.cards[satellite.norad_catalog_id])
        for catalog_id, card in self.cards.items():
            card.expansion_requested.connect(
                lambda expanded, selected=catalog_id: self._set_expanded_card(
                    selected, expanded
                )
            )
        self._set_expanded_card(all_satellites[0].norad_catalog_id, True)
        layout.addStretch()
        self.setWidget(content)

        context.bus.orbit_snapshot.connect(self._show_orbit)
        context.bus.tracking_error.connect(self._show_error)
        context.bus.satellite_enabled_changed.connect(self._show_enabled)
        context.bus.satellite_tracking_state_changed.connect(self._show_running)
        for catalog_id, snapshot in context.state.orbit_snapshots.items():
            if catalog_id in self.cards:
                self.cards[catalog_id].show_orbit(snapshot)
        for catalog_id, message in context.state.tracking_errors.items():
            if catalog_id in self.cards:
                self.cards[catalog_id].show_error(message)
        for catalog_id in context.state.running_satellite_ids:
            if catalog_id in self.cards:
                self.cards[catalog_id].set_running(True)

    def _set_expanded_card(self, catalog_id: int, expanded: bool) -> None:
        card = self.cards.get(catalog_id)
        if card is None:
            return
        if not expanded:
            card.set_expanded(False)
            return
        for current_id, current_card in self.cards.items():
            current_card.set_expanded(current_id == catalog_id)

    def _show_orbit(self, snapshot: OrbitSnapshot) -> None:
        card = self.cards.get(snapshot.satellite.norad_catalog_id)
        if card is not None:
            card.show_orbit(snapshot)

    def _show_error(self, catalog_id: int, message: str) -> None:
        card = self.cards.get(catalog_id)
        if card is not None:
            card.show_error(message)

    def _show_enabled(self, catalog_id: int, enabled: bool) -> None:
        card = self.cards.get(catalog_id)
        if card is not None:
            card.set_enabled(enabled)

    def _show_running(self, catalog_id: int, running: bool) -> None:
        card = self.cards.get(catalog_id)
        if card is not None:
            card.set_running(running)
