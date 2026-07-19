from __future__ import annotations

from PySide6.QtWidgets import QApplication

from gui import (
    AppEventBus,
    ApplicationState,
    MainGUI,
    METEOR_M_N2_3,
    METEOR_M_N2_4,
    PANEL_SPECS,
    PanelContext,
    PanelSpec,
)
from main import AppOrchestrator, SettingsStore
from panel_log import ApplicationLogController


class FakeReceiver:
    def __init__(self) -> None:
        self.running = False
        self.settings = None

    def update_settings(self, settings) -> None:
        self.settings = settings

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def take_latest(self):
        return None


class FakeTracking:
    def __init__(self) -> None:
        self.running = False
        self.last_error = None
        self.refreshes = 0

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def request_tle_refresh(self) -> None:
        self.refreshes += 1

    def take_latest(self):
        return None


def make_context(settings: SettingsStore) -> tuple[AppEventBus, PanelContext]:
    bus = AppEventBus()
    state = ApplicationState(
        settings.receiver_settings(), settings.receiver_location()
    )
    return bus, PanelContext(bus, state)


def test_import_failure_is_reported_in_mandatory_log(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SettingsStore(tmp_path / "settings.yaml")
    _, context = make_context(settings)
    broken = PanelSpec(
        "broken",
        "Broken dependency",
        "module_that_does_not_exist_for_meteor_m_test",
        "Used to verify panel-load errors.",
        0,
        0,
        1,
        1,
    )
    window = MainGUI(context, panel_specs=(broken,))
    log_controller = ApplicationLogController(window, settings)

    assert not window.load_panel("broken")
    app.processEvents()
    text = window.log_panel.output.toPlainText()
    assert "Could not load Broken dependency panel" in text
    assert "missing module or dependency" in text

    log_controller.shutdown()
    window.close()
    app.processEvents()


def test_tracking_service_follows_loaded_panel_demand(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SettingsStore(tmp_path / "settings.yaml")
    bus, context = make_context(settings)
    tracking_panel = PanelSpec(
        "tracking",
        "Tracking consumer",
        "panel_constellation",
        "A lightweight test consumer.",
        0,
        0,
        1,
        1,
        starts_tracking=True,
    )
    ordinary_panel = PanelSpec(
        "ordinary",
        "Ordinary consumer",
        "panel_constellation",
        "Does not need orbit propagation.",
        0,
        1,
        1,
        1,
    )
    window = MainGUI(context, panel_specs=(tracking_panel, ordinary_panel))
    receiver = FakeReceiver()
    tracking = {57166: FakeTracking(), 59051: FakeTracking()}
    orchestrator = AppOrchestrator(
        window, bus, context.state, settings, receiver, tracking
    )

    assert window.load_panel("ordinary")
    assert not any(service.running for service in tracking.values())
    assert window.load_panel("tracking")
    assert all(service.running for service in tracking.values())
    window.unload_panel("tracking")
    assert not any(service.running for service in tracking.values())
    assert settings.loaded_panels() == ("ordinary",)

    orchestrator.shutdown()
    window.close()
    app.processEvents()


def test_appearance_menu_applies_and_persists_theme(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SettingsStore(tmp_path / "settings.yaml")
    bus, context = make_context(settings)
    window = MainGUI(context)
    orchestrator = AppOrchestrator(
        window,
        bus,
        context.state,
        settings,
        FakeReceiver(),
        {57166: FakeTracking(), 59051: FakeTracking()},
    )
    for spec in PANEL_SPECS:
        assert window.load_panel(spec.key)

    window._appearance_actions["normal"].trigger()
    app.processEvents()
    assert settings.appearance_mode() == "normal"
    assert context.state.appearance_mode == "normal"
    assert app.styleSheet() == ""
    spectrum = window.panel_instance("spectrum")
    constellation = window.panel_instance("constellation")
    picture = window.panel_instance("picture")
    assert window.map_panel._appearance_mode == "normal"
    assert window.map_panel._theme.canvas.name() == "#eef2f5"
    assert spectrum.spectrum._appearance_mode == "normal"
    assert spectrum.waterfall._appearance_mode == "normal"
    assert constellation._appearance_mode == "normal"
    assert picture._appearance_mode == "normal"

    window._appearance_actions["dark"].trigger()
    app.processEvents()
    assert settings.appearance_mode() == "dark"
    assert context.state.appearance_mode == "dark"
    assert app.styleSheet()
    assert window.map_panel._appearance_mode == "dark"
    assert spectrum.spectrum._appearance_mode == "dark"
    assert spectrum.waterfall._appearance_mode == "dark"
    assert constellation._appearance_mode == "dark"
    assert picture._appearance_mode == "dark"

    orchestrator.shutdown()
    window.close()
    app.processEvents()


def test_satellite_panel_controls_both_satellites_and_tunes_receiver(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = SettingsStore(tmp_path / "settings.yaml")
    bus, context = make_context(settings)
    window = MainGUI(context)
    receiver = FakeReceiver()
    tracking = {57166: FakeTracking(), 59051: FakeTracking()}
    orchestrator = AppOrchestrator(
        window, bus, context.state, settings, receiver, tracking
    )
    enabled_requests = []
    tuning_requests = []
    bus.satellite_enabled_requested.connect(
        lambda catalog_id, enabled: enabled_requests.append((catalog_id, enabled))
    )
    bus.receiver_settings_requested.connect(tuning_requests.append)

    assert window.load_panel("satellite_tracking")
    panel = window.panel_instance("satellite_tracking")
    assert set(panel.cards) == {
        METEOR_M_N2_3.norad_catalog_id,
        METEOR_M_N2_4.norad_catalog_id,
    }

    first_card = panel.cards[METEOR_M_N2_3.norad_catalog_id]
    first_card.enabled.click()
    assert enabled_requests[-1] == (METEOR_M_N2_3.norad_catalog_id, False)
    assert not tracking[METEOR_M_N2_3.norad_catalog_id].running
    assert tracking[METEOR_M_N2_4.norad_catalog_id].running
    assert settings.enabled_satellite_ids() == {METEOR_M_N2_4.norad_catalog_id}

    second_card = panel.cards[METEOR_M_N2_4.norad_catalog_id]
    second_card.tune_button.click()
    assert tuning_requests[-1].center_frequency_hz == METEOR_M_N2_4.lrpt_frequency_hz
    assert context.state.receiver_settings.center_frequency_hz == (
        METEOR_M_N2_4.lrpt_frequency_hz
    )
    assert receiver.settings.center_frequency_hz == METEOR_M_N2_4.lrpt_frequency_hz

    second_card.info_button.click()
    app.processEvents()
    assert second_card._information_message.isVisible()
    second_card._information_message.close()
    orchestrator.shutdown()
    window.close()
    app.processEvents()
