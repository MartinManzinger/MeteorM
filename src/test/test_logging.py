from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication

from gui import AppEventBus, ApplicationState, MainGUI, PanelContext
from main import SettingsStore
from panel_log import ApplicationLogController


def test_logging_panel_filters_history_and_saves_visible_messages(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SettingsStore(tmp_path / "settings.yaml")
    state = ApplicationState(
        settings.receiver_settings(), settings.receiver_location()
    )
    window = MainGUI(
        PanelContext(AppEventBus(), state),
        log_verbosity=settings.log_verbosity(),
    )
    controller = ApplicationLogController(window, settings)
    logger = logging.getLogger("meteor_m.test_logging")

    logger.debug("debug detail")
    logger.info("receiver ready")
    app.processEvents()

    assert "receiver ready" in window.log_panel.output.toPlainText()
    assert "debug detail" not in window.log_panel.output.toPlainText()

    controller.set_verbosity("DEBUG")
    assert "debug detail" in window.log_panel.output.toPlainText()
    assert settings.log_verbosity() == "DEBUG"

    destination = tmp_path / "session.log"
    controller.save_log(str(destination))
    assert "receiver ready" in destination.read_text(encoding="utf-8")
    assert "debug detail" in destination.read_text(encoding="utf-8")

    controller.shutdown()
    window.close()
    app.processEvents()
