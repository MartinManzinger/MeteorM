"""Optional receiver-location panel with manual and pasted-coordinate input."""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui import PanelContext, ReceiverLocation


logger = logging.getLogger("meteor_m.panel.location")


_AT_COORDINATES = re.compile(
    r"@\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)"
)
_QUERY_COORDINATES = re.compile(
    r"(?:[?&](?:query|q|ll)=)\s*(-?\d{1,2}(?:\.\d+)?)"
    r"(?:%2C|,|\s+)\s*(-?\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
_PLAIN_COORDINATES = re.compile(
    r"(?<![\d.])(-?\d{1,2}(?:\.\d+)?)\s*[,;]\s*"
    r"(-?\d{1,3}(?:\.\d+)?)(?![\d.])"
)


def parse_coordinates(text: str) -> ReceiverLocation:
    """Parse `latitude, longitude` or a common Google Maps URL."""
    normalized = text.strip()
    for pattern in (_AT_COORDINATES, _QUERY_COORDINATES, _PLAIN_COORDINATES):
        match = pattern.search(normalized)
        if match is None:
            continue
        latitude, longitude = (float(value) for value in match.groups())
        if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
            return ReceiverLocation(latitude, longitude)
        raise ValueError("coordinates are outside latitude/longitude limits")
    raise ValueError("expected latitude, longitude or a Google Maps URL")


class Panel(QScrollArea):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        self.context = context
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        instructions = QLabel(
            "Enter coordinates, paste a Google Maps link, or right-click the map "
            "and choose ‘Set receiver position here’."
        )
        instructions.setWordWrap(True)
        instructions.setObjectName("dimLabel")
        layout.addWidget(instructions)

        form = QFormLayout()
        self.latitude = QDoubleSpinBox()
        self.latitude.setObjectName("receiverLatitude")
        self.latitude.setRange(-90, 90)
        self.latitude.setDecimals(6)
        self.latitude.setSuffix("°")
        self.longitude = QDoubleSpinBox()
        self.longitude.setObjectName("receiverLongitude")
        self.longitude.setRange(-180, 180)
        self.longitude.setDecimals(6)
        self.longitude.setSuffix("°")
        form.addRow("Latitude", self.latitude)
        form.addRow("Longitude", self.longitude)
        layout.addLayout(form)

        self.apply_button = QPushButton("Use manual coordinates")
        self.apply_button.setObjectName("primaryButton")
        layout.addWidget(self.apply_button)

        self.paste_input = QLineEdit()
        self.paste_input.setObjectName("coordinatePasteInput")
        self.paste_input.setPlaceholderText(
            "52.520008, 13.404954 or https://www.google.com/maps/@52.52,13.405,12z"
        )
        layout.addWidget(self.paste_input)
        paste_buttons = QHBoxLayout()
        self.clipboard_button = QPushButton("Paste clipboard")
        self.parse_button = QPushButton("Use pasted location")
        paste_buttons.addWidget(self.clipboard_button)
        paste_buttons.addWidget(self.parse_button)
        layout.addLayout(paste_buttons)
        self.message = QLabel()
        self.message.setObjectName("dimLabel")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch()
        self.setWidget(content)

        self.apply_button.clicked.connect(self._request_manual_location)
        self.clipboard_button.clicked.connect(self._paste_clipboard)
        self.parse_button.clicked.connect(self._parse_pasted_location)
        self.paste_input.returnPressed.connect(self._parse_pasted_location)
        context.bus.location_updated.connect(self._show_location)
        self._show_location(context.state.receiver_location)

    def _request_manual_location(self) -> None:
        self._request_location(
            ReceiverLocation(self.latitude.value(), self.longitude.value()),
            "Manual coordinates applied",
        )

    def _paste_clipboard(self) -> None:
        self.paste_input.setText(QApplication.clipboard().text())
        self._parse_pasted_location()

    def _parse_pasted_location(self) -> None:
        try:
            location = parse_coordinates(self.paste_input.text())
        except ValueError as error:
            self.message.setText(f"Could not read location: {error}")
            logger.warning("Could not parse pasted receiver location: %s", error)
            return
        self._set_fields(location)
        self._request_location(location, "Pasted coordinates applied")

    def _request_location(self, location: ReceiverLocation, message: str) -> None:
        self.message.setText(message)
        self.context.bus.location_requested.emit(location)

    def _show_location(self, location: ReceiverLocation) -> None:
        self._set_fields(location)
        self.message.setText(
            f"Current receiver: {location.latitude_deg:+.6f}, "
            f"{location.longitude_deg:+.6f}"
        )

    def _set_fields(self, location: ReceiverLocation) -> None:
        latitude_blocker = QSignalBlocker(self.latitude)
        longitude_blocker = QSignalBlocker(self.longitude)
        self.latitude.setValue(location.latitude_deg)
        self.longitude.setValue(location.longitude_deg)
        del latitude_blocker, longitude_blocker
