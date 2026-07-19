"""Optional controls and status for both Meteor-M tracking services."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui import METEOR_SATELLITES, OrbitSnapshot, PanelContext, SatelliteDefinition


logger = logging.getLogger("meteor_m.panel.satellite_tracking")


class SatelliteCard(QGroupBox):
    def __init__(self, satellite: SatelliteDefinition, context: PanelContext) -> None:
        super().__init__(satellite.display_name)
        self.satellite = satellite
        self.context = context
        catalog_id = satellite.norad_catalog_id
        layout = QVBoxLayout(self)

        self.enabled = QCheckBox(f"Active · NORAD {catalog_id}")
        self.enabled.setToolTip("Activate tracking and the colored map overlay")
        self.enabled.setObjectName(f"satelliteEnabled_{catalog_id}")
        self.enabled.setChecked(catalog_id in context.state.enabled_satellite_ids)
        layout.addWidget(self.enabled)

        grid = QGridLayout()
        self.worker = QLabel("STOPPED")
        self.position = QLabel("Waiting for orbital elements")
        self.projection = QLabel("--")
        self.source = QLabel("--")
        self.epoch = QLabel("--")
        self.retrieved = QLabel("--")
        self.frequency = QLabel(f"{satellite.lrpt_frequency_hz / 1e6:.4f} MHz")
        for row, (name, value) in enumerate(
            (
                ("Worker", self.worker),
                ("Actual position", self.position),
                ("Projected track", self.projection),
                ("TLE source", self.source),
                ("TLE epoch", self.epoch),
                ("Fetched", self.retrieved),
                ("LRPT tune", self.frequency),
            )
        ):
            label = QLabel(name)
            label.setObjectName("dimLabel")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setWordWrap(True)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.fetch_button = QPushButton("Fetch TLE")
        self.fetch_button.setObjectName(f"fetchTle_{catalog_id}")
        self.fetch_button.setToolTip(
            "Request this satellite from CelesTrak in the worker thread and store "
            "validated data in settings.yaml. The two-hour limit still applies."
        )
        self.tune_button = QPushButton("Tune SDR")
        self.tune_button.setObjectName(f"tuneSdr_{catalog_id}")
        self.tune_button.setToolTip(
            f"Set SDR center frequency to {satellite.lrpt_frequency_hz / 1e6:.4f} MHz"
        )
        self.info_button = QPushButton("Info…")
        self.info_button.setObjectName(f"satelliteInfo_{catalog_id}")
        buttons.addWidget(self.fetch_button)
        buttons.addWidget(self.tune_button)
        buttons.addWidget(self.info_button)
        layout.addLayout(buttons)

        self.enabled.toggled.connect(self._request_enabled)
        self.fetch_button.clicked.connect(
            lambda checked=False: context.bus.tle_refresh_requested.emit(catalog_id)
        )
        self.tune_button.clicked.connect(self._tune_receiver)
        self.info_button.clicked.connect(self._show_information)
        self._set_controls_enabled(self.enabled.isChecked())

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
        settings = replace(
            self.context.state.receiver_settings,
            center_frequency_hz=self.satellite.lrpt_frequency_hz,
        )
        self.context.bus.receiver_settings_requested.emit(settings)
        logger.info(
            "Requested SDR tune for %s at %.4f MHz",
            self.satellite.display_name,
            self.satellite.lrpt_frequency_hz / 1e6,
        )

    def _show_information(self) -> None:
        message = QMessageBox(self)
        self._information_message = message
        message.setWindowTitle(self.satellite.display_name)
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(
            f"<b>{self.satellite.display_name}</b><br>"
            f"NORAD catalog ID: {self.satellite.norad_catalog_id}<br>"
            f"Configured LRPT tune: {self.satellite.lrpt_frequency_hz / 1e6:.4f} MHz"
        )
        message.setInformativeText(
            f"{self.satellite.summary}\n\n"
            "Meteor LRPT can switch among its assigned frequencies; verify current "
            "transmission status when reception fails.\n\n"
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
        content = QWidget()
        layout = QVBoxLayout(content)
        explanation = QLabel(
            "Activate either satellite independently. Active workers calculate local "
            "SGP4 positions and send distinct overlays to the mandatory map."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("dimLabel")
        layout.addWidget(explanation)

        self.cards = {
            satellite.norad_catalog_id: SatelliteCard(satellite, context)
            for satellite in METEOR_SATELLITES
        }
        for card in self.cards.values():
            layout.addWidget(card)
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
