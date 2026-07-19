import pytest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QApplication

from panel_map import (
    OrbitMapWidget,
    Panel,
    half_hour_track_markers,
    historical_track_alpha,
)
from gui import (
    AppEventBus,
    ApplicationState,
    METEOR_M_N2_3,
    METEOR_M_N2_4,
    OrbitSnapshot,
    PanelContext,
    ReceiverLocation,
    ReceiverSettings,
    SatellitePosition,
)


def test_map_point_can_request_receiver_location() -> None:
    app = QApplication.instance() or QApplication([])
    widget = OrbitMapWidget()
    widget.resize(720, 360)
    selected = []
    widget.location_selected.connect(
        lambda latitude, longitude: selected.append((latitude, longitude))
    )

    widget.select_receiver_at(QPointF(widget.width() / 2, widget.height() / 2))

    assert selected[0][0] == pytest.approx(0.0)
    assert selected[0][1] == pytest.approx(0.0)
    widget.close()
    app.processEvents()


def test_map_context_menu_action_requests_receiver_location(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    widget = OrbitMapWidget()
    widget.resize(720, 360)
    selected = []
    widget.location_selected.connect(
        lambda latitude, longitude: selected.append((latitude, longitude))
    )

    class ContextEvent:
        @staticmethod
        def pos():
            return QPoint(360, 180)

        @staticmethod
        def globalPos():
            return QPoint(100, 100)

    monkeypatch.setattr(
        widget,
        "_show_receiver_menu",
        lambda map_position, global_position: widget.select_receiver_at(map_position),
    )
    widget.contextMenuEvent(ContextEvent())

    assert selected[0][0] == pytest.approx(0.0)
    assert selected[0][1] == pytest.approx(0.0)
    widget.close()
    app.processEvents()


def test_right_clicking_satellite_requests_its_sdr_frequency(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    bus = AppEventBus()
    state = ApplicationState(ReceiverSettings(), ReceiverLocation())
    widget = Panel(PanelContext(bus, state))
    widget.resize(720, 360)
    observed = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    position = SatellitePosition(observed, 0.0, 0.0, 820.0)
    snapshot = OrbitSnapshot(
        satellite=METEOR_M_N2_4,
        position=position,
        ground_track=(position,),
        tle_epoch=observed,
        tle_retrieved_at=observed,
        using_cached_tle=True,
    )
    widget.set_snapshot(snapshot)
    requested = []
    bus.receiver_settings_requested.connect(requested.append)

    class ContextEvent:
        @staticmethod
        def pos():
            return QPoint(360, 180)

        @staticmethod
        def globalPos():
            return QPoint(100, 100)

    monkeypatch.setattr(
        widget,
        "_show_satellite_menu",
        lambda selected, map_position, global_position: (
            widget.satellite_tune_requested.emit(
                selected.satellite.norad_catalog_id
            )
        ),
    )
    widget.contextMenuEvent(ContextEvent())

    assert requested[-1].center_frequency_hz == METEOR_M_N2_4.lrpt_frequency_hz
    widget.close()
    app.processEvents()


def test_map_keeps_distinct_overlays_for_both_satellites() -> None:
    app = QApplication.instance() or QApplication([])
    widget = OrbitMapWidget()
    observed = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    for index, satellite in enumerate((METEOR_M_N2_3, METEOR_M_N2_4)):
        position = SatellitePosition(observed, 10.0 + index, 20.0 + index, 820.0)
        widget.set_snapshot(
            OrbitSnapshot(
                satellite=satellite,
                position=position,
                ground_track=(position,),
                tle_epoch=observed,
                tle_retrieved_at=observed,
                using_cached_tle=True,
            )
        )

    assert set(widget._snapshots) == {57166, 59051}
    assert METEOR_M_N2_3.map_color != METEOR_M_N2_4.map_color
    widget.remove_satellite(57166)
    assert set(widget._snapshots) == {59051}
    widget.close()
    app.processEvents()


def test_track_markers_are_interpolated_at_local_half_hours() -> None:
    start = datetime(2026, 7, 19, 9, 42, 7, tzinfo=UTC)
    track = tuple(
        SatellitePosition(
            start + timedelta(seconds=12 * index),
            latitude_deg=index / 5,
            longitude_deg=-30.0 + index / 10,
            altitude_km=820.0,
        )
        for index in range(351)
    )

    markers = half_hour_track_markers(track, ZoneInfo("Europe/Berlin"))

    assert [marker.label for marker in markers] == ["12:00", "12:30"]
    assert [marker.position.observed_at for marker in markers] == [
        datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
        datetime(2026, 7, 19, 10, 30, tzinfo=UTC),
    ]
    assert markers[0].position.latitude_deg == pytest.approx(17.8833, abs=0.001)


def test_historical_track_opacity_increases_toward_current_position() -> None:
    current = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    oldest = current - timedelta(minutes=90)

    oldest_alpha = historical_track_alpha(oldest, current, oldest)
    middle_alpha = historical_track_alpha(
        current - timedelta(minutes=45), current, oldest
    )
    current_alpha = historical_track_alpha(current, current, oldest)
    future_alpha = historical_track_alpha(
        current + timedelta(minutes=30), current, oldest
    )

    assert oldest_alpha < middle_alpha < current_alpha
    assert middle_alpha < (oldest_alpha + current_alpha) / 2
    assert current_alpha == future_alpha == 225
