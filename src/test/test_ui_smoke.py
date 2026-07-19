import pytest


pytest.importorskip("PySide6.QtWidgets", reason="PySide6 Qt runtime is not installed")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from gui import (
    APPLICATION_ICON_PATH,
    AppEventBus,
    ApplicationState,
    MainGUI,
    PANEL_SPECS,
    PanelContext,
    ReceiverLocation,
    ReceiverSettings,
)


def make_window() -> MainGUI:
    state = ApplicationState(ReceiverSettings(), ReceiverLocation(52.52, 13.405))
    return MainGUI(PanelContext(AppEventBus(), state))


def test_gui_starts_with_the_mandatory_map_and_log_panels() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()

    assert window.loaded_panel_keys() == ()
    log_slot = window.panel_slot("log")
    map_slot = window.panel_slot("map")
    assert log_slot is not None
    assert map_slot is not None
    assert not log_slot.close_button.isVisible()
    assert not map_slot.close_button.isVisible()
    assert log_slot.panel is window.log_panel
    assert map_slot.panel is window.map_panel
    assert window.map_panel is not None
    assert window.findChild(QWidget, "panelLoader") is None

    window.close()
    app.processEvents()


def test_icon_and_settings_share_the_custom_window_title_strip() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()

    assert APPLICATION_ICON_PATH.is_file()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not window.windowIcon().isNull()
    assert window.title_bar.icon_label.pixmap() is not None
    assert not window.title_bar.icon_label.pixmap().isNull()
    assert window.title_bar.settings_button.menu() is window.settings_menu
    assert window.menuWidget() is None
    assert set(window._appearance_actions) == {"green", "dark", "normal"}

    window.close()
    app.processEvents()


def test_every_optional_panel_loads_and_unloads_independently() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()

    for spec in PANEL_SPECS:
        assert window.load_panel(spec.key)
        assert window.panel_instance(spec.key) is not None

    assert window.panel_instance("location") is None
    assert window.loaded_panel_keys() == tuple(
        spec.key for spec in PANEL_SPECS if spec.key != "location"
    )

    for spec in PANEL_SPECS:
        window.unload_panel(spec.key)
        assert window.panel_instance(spec.key) is None

    assert window.loaded_panel_keys() == ()
    window.close()
    app.processEvents()


def test_satellite_and_receiver_panels_share_one_exclusive_slot() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()
    shared_slot = window.panel_slot("satellite_tracking")

    assert shared_slot is window.panel_slot("location")
    assert window.load_panel("satellite_tracking")
    assert window.panel_instance("satellite_tracking") is not None
    assert window.panel_instance("location") is None

    shared_slot.choice_buttons["location"].click()
    app.processEvents()
    assert window.panel_instance("satellite_tracking") is None
    assert window.panel_instance("location") is not None
    assert window.loaded_panel_keys() == ("location",)

    shared_slot.choice_buttons["satellite_tracking"].click()
    app.processEvents()
    assert window.panel_instance("location") is None
    assert window.panel_instance("satellite_tracking") is not None
    assert window.loaded_panel_keys() == ("satellite_tracking",)

    window.close()
    app.processEvents()


def test_closing_a_panel_leaves_a_clickable_slot_that_reopens_it() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()
    assert window.load_panel("location")

    slot = window.panel_slot("location")
    assert slot is not None
    slot.close_button.click()
    app.processEvents()

    assert window.panel_instance("location") is None
    assert window.loaded_panel_keys() == ()
    assert slot.placeholder.isEnabled()
    assert "Not loaded" in slot.placeholder.text()
    slot.placeholder.click()
    app.processEvents()
    assert window.panel_instance("location") is not None
    window.close()
    app.processEvents()


def test_panel_grid_matches_panel_layout_spreadsheet() -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window()

    assert {
        key: window.panel_layout_position(key)
        for key in (
            "satellite_tracking",
            "map",
            "spectrum",
            "constellation",
            "location",
            "sdr",
            "log",
            "picture",
        )
    } == {
        "satellite_tracking": (0, 0, 4, 1),
        "map": (0, 1, 4, 2),
        "spectrum": (0, 3, 4, 2),
        "constellation": (0, 5, 4, 1),
        "location": (0, 0, 4, 1),
        "sdr": (4, 0, 4, 2),
        "log": (4, 2, 4, 2),
        "picture": (4, 4, 4, 2),
    }

    window.close()
    app.processEvents()
