"""Optional SDR and HackRF One control panel."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import PanelContext, ReceiverSettings


class Panel(QWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        self.context = context
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.device = QComboBox()
        self.device.addItems(("Simulated HackRF One", "No physical device connected"))
        self.frequency = self._double_input(1.0, 6000.0, 3, " MHz")
        self.sample_rate = self._double_input(2.0, 20.0, 2, " MS/s")
        self.bandwidth = self._double_input(1.0, 28.0, 2, " MHz")
        self.lna_gain = QSpinBox()
        self.lna_gain.setRange(0, 40)
        self.lna_gain.setSingleStep(8)
        self.lna_gain.setSuffix(" dB")
        self.vga_gain = QSpinBox()
        self.vga_gain.setRange(0, 62)
        self.vga_gain.setSingleStep(2)
        self.vga_gain.setSuffix(" dB")
        self.amplifier = QCheckBox("Enable RF amplifier")
        self.apply_button = QPushButton("Apply receiver settings")
        for label, widget in (
            ("Device", self.device),
            ("Center", self.frequency),
            ("Sample rate", self.sample_rate),
            ("Bandwidth", self.bandwidth),
            ("LNA gain", self.lna_gain),
            ("VGA gain", self.vga_gain),
            ("Amplifier", self.amplifier),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        layout.addWidget(self.apply_button)
        layout.addStretch()
        self.receiver_button = QPushButton()
        self.receiver_button.setObjectName("primaryButton")
        self.receiver_button.setMinimumHeight(42)
        layout.addWidget(self.receiver_button)

        self.apply_button.clicked.connect(self._apply)
        self.receiver_button.clicked.connect(self._toggle_receiver)
        context.bus.receiver_running_changed.connect(self._show_running)
        context.bus.receiver_settings_updated.connect(self._show_settings)
        self._show_settings(context.state.receiver_settings)
        self._show_running(context.state.receiver_running)

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

    def _apply(self) -> None:
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

    def _show_settings(self, settings: ReceiverSettings) -> None:
        if self.device.findText(settings.device) < 0:
            self.device.addItem(settings.device)
        self.device.setCurrentText(settings.device)
        self.frequency.setValue(settings.center_frequency_hz / 1e6)
        self.sample_rate.setValue(settings.sample_rate_hz / 1e6)
        self.bandwidth.setValue(settings.filter_bandwidth_hz / 1e6)
        self.lna_gain.setValue(settings.lna_gain_db)
        self.vga_gain.setValue(settings.vga_gain_db)
        self.amplifier.setChecked(settings.amplifier_enabled)

    @staticmethod
    def _double_input(
        minimum: float, maximum: float, decimals: int, suffix: str
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(decimals)
        field.setSuffix(suffix)
        return field
