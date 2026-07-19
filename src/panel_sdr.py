"""Optional SDR and HackRF One control panel."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import (
    HACKRF_AUTO_DEVICE,
    RECEIVER_SAMPLE_RATE_MAX_HZ,
    RECEIVER_SAMPLE_RATE_MIN_HZ,
    SIMULATION_DEVICE,
    PanelContext,
    ReceiverSettings,
    ReceiverStatus,
)


class Panel(QWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        self.context = context
        self._updating_controls = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.device = QComboBox()
        self.device.addItems(context.state.receiver_devices)
        self.refresh_devices = QPushButton("Discover")
        device_row = QWidget()
        device_layout = QHBoxLayout(device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.addWidget(self.device, 1)
        device_layout.addWidget(self.refresh_devices)
        self.frequency = self._double_input(1.0, 6000.0, 3, " MHz")
        self.frequency_slider = self._double_slider(
            self.frequency, scale=1000, page_step=1000
        )
        self.sample_rate = self._double_input(
            RECEIVER_SAMPLE_RATE_MIN_HZ / 1e6,
            RECEIVER_SAMPLE_RATE_MAX_HZ / 1e6,
            0,
            " MS/s",
        )
        self.sample_rate.setSingleStep(1.0)
        self.sample_rate_slider = self._double_slider(
            self.sample_rate, scale=1, page_step=1
        )
        self.bandwidth = self._double_input(1.0, 28.0, 2, " MHz")
        self.bandwidth_slider = self._double_slider(
            self.bandwidth, scale=100, page_step=10
        )
        self.lna_gain = QSpinBox()
        self.lna_gain.setRange(0, 40)
        self.lna_gain.setSingleStep(8)
        self.lna_gain.setSuffix(" dB")
        self.lna_gain_slider = self._gain_slider(self.lna_gain, step=8)
        self.vga_gain = QSpinBox()
        self.vga_gain.setRange(0, 62)
        self.vga_gain.setSingleStep(2)
        self.vga_gain.setSuffix(" dB")
        self.vga_gain_slider = self._gain_slider(self.vga_gain, step=2)
        self.amplifier = QCheckBox("Enable RF amplifier")
        for label, widget in (
            ("Device", device_row),
            ("Center", self._control_row(self.frequency, self.frequency_slider)),
            (
                "Sample rate",
                self._control_row(self.sample_rate, self.sample_rate_slider),
            ),
            (
                "Bandwidth",
                self._control_row(self.bandwidth, self.bandwidth_slider),
            ),
            ("LNA gain", self._control_row(self.lna_gain, self.lna_gain_slider)),
            ("VGA gain", self._control_row(self.vga_gain, self.vga_gain_slider)),
            ("Amplifier", self.amplifier),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.backend_status = QLabel()
        self.backend_status.setWordWrap(True)
        self.sample_status = QLabel()
        layout.addWidget(self.backend_status)
        layout.addWidget(self.sample_status)
        layout.addStretch()
        self.receiver_button = QPushButton()
        self.receiver_button.setObjectName("primaryButton")
        self.receiver_button.setMinimumHeight(42)
        layout.addWidget(self.receiver_button)

        self.refresh_devices.clicked.connect(
            context.bus.receiver_devices_requested.emit
        )
        self.receiver_button.clicked.connect(self._toggle_receiver)
        context.bus.receiver_running_changed.connect(self._show_running)
        context.bus.receiver_settings_updated.connect(self._show_settings)
        context.bus.receiver_devices_updated.connect(self._show_devices)
        context.bus.receiver_status_changed.connect(self._show_status)
        self.device.currentTextChanged.connect(self._settings_changed)
        self.frequency.valueChanged.connect(self._settings_changed)
        self.sample_rate.valueChanged.connect(self._settings_changed)
        self.bandwidth.valueChanged.connect(self._settings_changed)
        self.lna_gain.valueChanged.connect(self._settings_changed)
        self.vga_gain.valueChanged.connect(self._settings_changed)
        self.amplifier.toggled.connect(self._settings_changed)
        self._show_settings(context.state.receiver_settings)
        self._show_running(context.state.receiver_running)
        self._show_status(context.state.receiver_status)

    def current_settings(self) -> ReceiverSettings:
        return ReceiverSettings(
            center_frequency_hz=self.frequency.value() * 1e6,
            sample_rate_hz=self.sample_rate.value() * 1e6,
            filter_bandwidth_hz=self.bandwidth.value() * 1e6,
            lna_gain_db=self.lna_gain.value(),
            vga_gain_db=self.vga_gain.value(),
            amplifier_enabled=self.amplifier.isChecked(),
            device=self.device.currentText(),
        )

    def _settings_changed(self, _value: object = None) -> None:
        if not self._updating_controls:
            self.context.bus.receiver_settings_requested.emit(self.current_settings())

    def _toggle_receiver(self) -> None:
        signal = (
            self.context.bus.receiver_stop_requested
            if self.context.state.receiver_running
            else self.context.bus.receiver_start_requested
        )
        signal.emit()

    def _show_running(self, running: bool) -> None:
        self.receiver_button.setText("Stop receiver" if running else "Start receiver")

    def _show_devices(self, devices: object) -> None:
        previous_update_state = self._updating_controls
        self._updating_controls = True
        selected = self.device.currentText()
        try:
            choices = tuple(str(device) for device in devices)
            if SIMULATION_DEVICE not in choices:
                choices = (SIMULATION_DEVICE, *choices)
            if HACKRF_AUTO_DEVICE not in choices:
                choices = (*choices, HACKRF_AUTO_DEVICE)
            self.device.clear()
            self.device.addItems(choices)
            if selected in choices:
                self.device.setCurrentText(selected)
            elif self.context.state.receiver_settings.device in choices:
                self.device.setCurrentText(self.context.state.receiver_settings.device)
        finally:
            self._updating_controls = previous_update_state

    def _show_status(self, status: ReceiverStatus) -> None:
        self.backend_status.setText(
            f"{status.backend} · {status.state.upper()} — {status.message}"
        )
        self.sample_status.setText(
            f"Analysis samples: {status.samples_received:,} · "
            f"dropped analysis buffers: {status.dropped_buffers:,}"
        )
        hardware_busy = status.backend.startswith("HackRF") and status.active
        self.refresh_devices.setEnabled(not hardware_busy)

    def _show_settings(self, settings: ReceiverSettings) -> None:
        previous_update_state = self._updating_controls
        self._updating_controls = True
        try:
            if self.device.findText(settings.device) < 0:
                self.device.addItem(settings.device)
            self.device.setCurrentText(settings.device)
            self.frequency.setValue(settings.center_frequency_hz / 1e6)
            self.sample_rate.setValue(settings.sample_rate_hz / 1e6)
            self.bandwidth.setValue(settings.filter_bandwidth_hz / 1e6)
            self.lna_gain.setValue(settings.lna_gain_db)
            self.vga_gain.setValue(settings.vga_gain_db)
            self.amplifier.setChecked(settings.amplifier_enabled)
        finally:
            self._updating_controls = previous_update_state

    @staticmethod
    def _double_input(
        minimum: float, maximum: float, decimals: int, suffix: str
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(decimals)
        field.setSuffix(suffix)
        return field

    @staticmethod
    def _control_row(field: QWidget, slider: QSlider) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        field.setMinimumWidth(105)
        layout.addWidget(field)
        layout.addWidget(slider, 1)
        return row

    @classmethod
    def _double_slider(
        cls, field: QDoubleSpinBox, *, scale: int, page_step: int
    ) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(
            round(field.minimum() * scale), round(field.maximum() * scale)
        )
        slider.setSingleStep(1)
        slider.setPageStep(page_step)
        slider.valueChanged.connect(lambda value: field.setValue(value / scale))
        field.valueChanged.connect(
            lambda value: cls._set_slider_without_signal(
                slider, round(value * scale)
            )
        )
        return slider

    @classmethod
    def _gain_slider(cls, field: QSpinBox, *, step: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(field.minimum(), field.maximum())
        slider.setSingleStep(step)
        slider.setPageStep(step)

        def set_gain(value: int) -> None:
            snapped = max(
                field.minimum(),
                min(field.maximum(), round(value / step) * step),
            )
            cls._set_slider_without_signal(slider, snapped)
            field.setValue(snapped)

        slider.valueChanged.connect(set_gain)
        field.valueChanged.connect(
            lambda value: cls._set_slider_without_signal(slider, value)
        )
        return slider

    @staticmethod
    def _set_slider_without_signal(slider: QSlider, value: int) -> None:
        previous = slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(previous)
