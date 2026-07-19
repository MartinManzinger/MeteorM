"""Mandatory application log panel and its logging bridge."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import PanelContext


class Panel(QWidget):
    verbosity_changed = Signal(str)
    save_requested = Signal(str)

    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Verbosity"))
        self.verbosity = QComboBox()
        self.verbosity.setObjectName("logVerbosity")
        self.verbosity.addItems(("ERROR", "WARNING", "INFO", "DEBUG"))
        self.verbosity.setCurrentText(context.state.log_verbosity.upper())
        toolbar.addWidget(self.verbosity)
        toolbar.addStretch()
        self.save_button = QPushButton("Save log…")
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)

        self.output = QPlainTextEdit()
        self.output.setObjectName("applicationLog")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        self.output.setPlaceholderText("Application messages will appear here")
        layout.addWidget(self.output, 1)

        self.verbosity.currentTextChanged.connect(self.verbosity_changed.emit)
        self.save_button.clicked.connect(self._choose_save_path)

    def append_message(self, message: str) -> None:
        self.output.appendPlainText(message)

    def replace_messages(self, messages: tuple[str, ...]) -> None:
        self.output.setPlainText("\n".join(messages))

    def _choose_save_path(self) -> None:
        default_name = f"meteor-m-{datetime.now().astimezone():%Y%m%d-%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save application log",
            default_name,
            "Log files (*.log);;Text files (*.txt);;All files (*)",
        )
        if path:
            self.save_requested.emit(path)


class _LogEmitter(QObject):
    message_emitted = Signal(str, int)


class GuiLogHandler(logging.Handler):
    def __init__(self, capacity: int = 5000) -> None:
        super().__init__(logging.DEBUG)
        self.emitter = _LogEmitter()
        self._records: deque[tuple[int, str]] = deque(maxlen=capacity)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._records.append((record.levelno, message))
            self.emitter.message_emitted.emit(message, record.levelno)
        except Exception:
            self.handleError(record)

    def messages(self, minimum_level: int) -> tuple[str, ...]:
        self.acquire()
        try:
            return tuple(
                message for level, message in self._records if level >= minimum_level
            )
        finally:
            self.release()

    def save(self, path: Path, minimum_level: int) -> int:
        messages = self.messages(minimum_level)
        contents = "\n".join(messages)
        if contents:
            contents += "\n"
        path.write_text(contents, encoding="utf-8")
        return len(messages)


class ApplicationLogController(QObject):
    LEVELS = {
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }

    def __init__(self, window, settings=None) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings
        verbosity = settings.log_verbosity() if settings is not None else "INFO"
        self.minimum_level = self.LEVELS[verbosity]
        self.handler = GuiLogHandler()
        self.logger = logging.getLogger("meteor_m")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(self.handler)
        self.handler.emitter.message_emitted.connect(
            self._receive_message, Qt.ConnectionType.QueuedConnection
        )
        window.log_panel.verbosity_changed.connect(self.set_verbosity)
        window.log_panel.save_requested.connect(self.save_log)

    @Slot(str, int)
    def _receive_message(self, message: str, level: int) -> None:
        if level >= self.minimum_level:
            self.window.log_panel.append_message(message)

    @Slot(str)
    def set_verbosity(self, name: str) -> None:
        self.minimum_level = self.LEVELS.get(name, logging.INFO)
        if self.settings is not None:
            self.settings.update_log_verbosity(name)
        self.window.log_panel.replace_messages(
            self.handler.messages(self.minimum_level)
        )
        self.logger.info("Log verbosity changed to %s", name)

    @Slot(str)
    def save_log(self, path: str) -> None:
        try:
            count = self.handler.save(Path(path), self.minimum_level)
        except OSError as error:
            self.logger.error("Could not save log to %s: %s", path, error)
            return
        self.logger.info("Saved %d log messages to %s", count, path)

    def shutdown(self) -> None:
        self.logger.info("Application logging stopped")
        self.logger.removeHandler(self.handler)
        self.handler.close()
