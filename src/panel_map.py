"""Mandatory offline world-map panel and its Natural Earth renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

from gui import OrbitSnapshot, PanelContext, ReceiverLocation, SatellitePosition


Coordinate = tuple[float, float]
Ring = tuple[Coordinate, ...]


@dataclass(frozen=True, slots=True)
class GeoPolygon:
    exterior: Ring
    holes: tuple[Ring, ...] = ()


class MapGeometryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrackTimeMarker:
    position: SatellitePosition
    label: str


@dataclass(frozen=True, slots=True)
class MapTheme:
    canvas: QColor
    ocean_start: QColor
    ocean_middle: QColor
    ocean_end: QColor
    grid: QColor
    land_fill: QColor
    land_glow: QColor
    land_outline: QColor
    coordinate_text: QColor
    overlay_background: QColor
    overlay_text: QColor
    border: QColor
    current_outline: QColor
    current_fill: QColor
    receiver_text: QColor
    label_shadow: QColor


def map_theme(appearance_mode: str) -> MapTheme:
    if appearance_mode == "normal":
        return MapTheme(
            canvas=QColor("#eef2f5"),
            ocean_start=QColor("#dceff6"),
            ocean_middle=QColor("#d4e8f0"),
            ocean_end=QColor("#cadfe8"),
            grid=QColor(84, 121, 137, 105),
            land_fill=QColor("#c5d8c4"),
            land_glow=QColor(75, 125, 91, 55),
            land_outline=QColor("#668d70"),
            coordinate_text=QColor(57, 84, 96, 175),
            overlay_background=QColor(255, 255, 255, 220),
            overlay_text=QColor("#243d48"),
            border=QColor("#849eaa"),
            current_outline=QColor("#203945"),
            current_fill=QColor("#f8fbfc"),
            receiver_text=QColor("#503515"),
            label_shadow=QColor(255, 255, 255, 225),
        )
    return MapTheme(
        canvas=QColor("#070e15"),
        ocean_start=QColor("#0d3344"),
        ocean_middle=QColor("#092936"),
        ocean_end=QColor("#071f2b"),
        grid=QColor(42, 88, 105, 135),
        land_fill=QColor("#183d3a"),
        land_glow=QColor(77, 146, 121, 65),
        land_outline=QColor("#599b80"),
        coordinate_text=QColor(117, 157, 169, 150),
        overlay_background=QColor(4, 15, 22, 195),
        overlay_text=QColor("#b7cad4"),
        border=QColor("#315468"),
        current_outline=QColor("#eafaff"),
        current_fill=QColor("#07131c"),
        receiver_text=QColor("#fff7e8"),
        label_shadow=QColor(2, 10, 15, 210),
    )


def half_hour_track_markers(
    track: tuple[SatellitePosition, ...],
    local_timezone: tzinfo | None = None,
) -> tuple[TrackTimeMarker, ...]:
    """Interpolate positions at local wall-clock XX:00 and XX:30 boundaries."""
    if len(track) < 2:
        return ()
    ordered = tuple(sorted(track, key=lambda item: item.observed_at))
    start = ordered[0].observed_at
    end = ordered[-1].observed_at
    local_start = start.astimezone(local_timezone)
    boundary = local_start.replace(
        minute=0 if local_start.minute < 30 else 30,
        second=0,
        microsecond=0,
    )
    if boundary < local_start:
        boundary += timedelta(minutes=30)

    markers: list[TrackTimeMarker] = []
    track_index = 0
    while True:
        target = boundary.astimezone(UTC)
        if target > end:
            break
        while (
            track_index + 1 < len(ordered)
            and ordered[track_index + 1].observed_at < target
        ):
            track_index += 1
        if track_index + 1 >= len(ordered):
            break
        before = ordered[track_index]
        after = ordered[track_index + 1]
        if before.observed_at <= target <= after.observed_at:
            markers.append(
                TrackTimeMarker(
                    _interpolate_position(before, after, target),
                    boundary.strftime("%H:%M"),
                )
            )
        boundary += timedelta(minutes=30)
    return tuple(markers)


def _interpolate_position(
    before: SatellitePosition,
    after: SatellitePosition,
    observed_at: datetime,
) -> SatellitePosition:
    interval = (after.observed_at - before.observed_at).total_seconds()
    fraction = (
        0.0
        if interval <= 0
        else (observed_at - before.observed_at).total_seconds() / interval
    )
    fraction = max(0.0, min(1.0, fraction))
    longitude_delta = (
        (after.longitude_deg - before.longitude_deg + 180.0) % 360.0
    ) - 180.0
    longitude = before.longitude_deg + fraction * longitude_delta
    longitude = (longitude + 180.0) % 360.0 - 180.0
    return SatellitePosition(
        observed_at=observed_at,
        latitude_deg=before.latitude_deg
        + fraction * (after.latitude_deg - before.latitude_deg),
        longitude_deg=longitude,
        altitude_km=before.altitude_km
        + fraction * (after.altitude_km - before.altitude_km),
    )


def historical_track_alpha(
    observed_at: datetime,
    current_time: datetime,
    oldest_time: datetime,
    *,
    minimum: int = 24,
    maximum: int = 225,
) -> int:
    """Return increasing opacity from the oldest past point to the present."""
    if observed_at >= current_time:
        return maximum
    span = max(1e-6, (current_time - oldest_time).total_seconds())
    progress = (observed_at - oldest_time).total_seconds() / span
    progress = max(0.0, min(1.0, progress)) ** 2.2
    return round(minimum + progress * (maximum - minimum))


@lru_cache(maxsize=1)
def load_land_geometry() -> tuple[GeoPolygon, ...]:
    asset = Path(__file__).with_name("ne_110m_land.geojson")
    try:
        payload = json.loads(asset.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MapGeometryError(f"could not load bundled land geometry: {error}") from error
    if payload.get("type") != "FeatureCollection":
        raise MapGeometryError("land geometry must be a GeoJSON FeatureCollection")
    polygons: list[GeoPolygon] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            polygons.append(_parse_polygon(coordinates))
        elif geometry_type == "MultiPolygon":
            if not isinstance(coordinates, list):
                raise MapGeometryError("MultiPolygon coordinates must be a list")
            polygons.extend(_parse_polygon(raw) for raw in coordinates)
        else:
            raise MapGeometryError(f"unsupported land geometry: {geometry_type!r}")
    if not polygons:
        raise MapGeometryError("land geometry contains no polygons")
    return tuple(polygons)


def _parse_polygon(raw: Any) -> GeoPolygon:
    if not isinstance(raw, list) or not raw:
        raise MapGeometryError("Polygon must contain at least one ring")
    rings = tuple(_parse_ring(ring) for ring in raw)
    return GeoPolygon(exterior=rings[0], holes=rings[1:])


def _parse_ring(raw: Any) -> Ring:
    if not isinstance(raw, list) or len(raw) < 4:
        raise MapGeometryError("linear ring must contain at least four coordinates")
    result: list[Coordinate] = []
    for point in raw:
        if not isinstance(point, list) or len(point) < 2:
            raise MapGeometryError("invalid coordinate in land geometry")
        longitude, latitude = float(point[0]), float(point[1])
        if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
            raise MapGeometryError("land coordinate lies outside WGS84 bounds")
        result.append((longitude, latitude))
    if result[0] != result[-1]:
        result.append(result[0])
    return tuple(result)


class OrbitMapWidget(QWidget):
    location_selected = Signal(float, float)
    satellite_tune_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._location = ReceiverLocation()
        self._snapshots: dict[int, OrbitSnapshot] = {}
        self._tracking_errors: dict[int, str] = {}
        self._tracking_active = False
        self._appearance_mode = "dark"
        self._theme = map_theme(self._appearance_mode)
        self._land_geometry = load_land_geometry()
        self._land_path: QPainterPath | None = None
        self._land_path_key: tuple[float, float, float, float] | None = None
        self.setMinimumSize(520, 255)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(
            "Right-click a satellite to tune the SDR; right-click elsewhere "
            "to set the receiver location"
        )

    def set_appearance(self, appearance_mode: str) -> None:
        self._appearance_mode = "normal" if appearance_mode == "normal" else "dark"
        self._theme = map_theme(self._appearance_mode)
        self.update()

    def set_location(self, location: ReceiverLocation) -> None:
        self._location = location
        self.update()

    def set_snapshot(self, snapshot: OrbitSnapshot) -> None:
        catalog_id = snapshot.satellite.norad_catalog_id
        self._snapshots[catalog_id] = snapshot
        self._tracking_errors.pop(catalog_id, None)
        self.update()

    def remove_satellite(self, norad_catalog_id: int) -> None:
        self._snapshots.pop(norad_catalog_id, None)
        self._tracking_errors.pop(norad_catalog_id, None)
        self.update()

    def set_tracking_error(self, norad_catalog_id: int, message: str) -> None:
        self._tracking_errors[norad_catalog_id] = message
        self.update()

    def set_tracking_active(self, active: bool) -> None:
        self._tracking_active = active
        self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        map_position = QPointF(event.pos())
        snapshot = self._satellite_at(map_position)
        if snapshot is not None:
            self._show_satellite_menu(snapshot, map_position, event.globalPos())
        else:
            self._show_receiver_menu(map_position, event.globalPos())

    def _satellite_at(self, point: QPointF) -> OrbitSnapshot | None:
        bounds = self._map_bounds()
        nearest: tuple[float, OrbitSnapshot] | None = None
        for snapshot in self._snapshots.values():
            satellite_point = self._to_point(
                snapshot.position.latitude_deg,
                snapshot.position.longitude_deg,
                bounds,
            )
            distance = (
                (satellite_point.x() - point.x()) ** 2
                + (satellite_point.y() - point.y()) ** 2
            ) ** 0.5
            if distance <= 14.0 and (nearest is None or distance < nearest[0]):
                nearest = distance, snapshot
        return nearest[1] if nearest is not None else None

    def _show_satellite_menu(
        self,
        snapshot: OrbitSnapshot,
        map_position: QPointF,
        global_position,
    ) -> None:
        menu = QMenu(self)
        satellite = snapshot.satellite
        tune = menu.addAction(
            f"Tune SDR to {satellite.lrpt_frequency_hz / 1e6:.4f} MHz "
            f"({satellite.short_name})"
        )
        tune.triggered.connect(
            lambda checked=False: self.satellite_tune_requested.emit(
                satellite.norad_catalog_id
            )
        )
        menu.addSeparator()
        select_location = menu.addAction("Set receiver position here")
        select_location.triggered.connect(
            lambda checked=False: self.select_receiver_at(map_position)
        )
        menu.exec(global_position)

    def _show_receiver_menu(self, map_position: QPointF, global_position) -> None:
        menu = QMenu(self)
        select_location = menu.addAction("Set receiver position here")
        select_location.triggered.connect(
            lambda checked=False: self.select_receiver_at(map_position)
        )
        menu.exec(global_position)

    def select_receiver_at(self, point: QPointF) -> None:
        latitude, longitude = self._to_geo(point)
        self.location_selected.emit(latitude, longitude)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._theme.canvas)
        bounds = self._map_bounds()
        ocean = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        ocean.setColorAt(0.0, self._theme.ocean_start)
        ocean.setColorAt(0.55, self._theme.ocean_middle)
        ocean.setColorAt(1.0, self._theme.ocean_end)
        painter.fillRect(bounds, ocean)
        painter.setClipRect(bounds)
        self._draw_grid(painter, bounds)
        self._draw_land(painter, bounds)
        self._draw_coordinate_labels(painter, bounds)

        for snapshot in self._snapshots.values():
            color = self._satellite_color(snapshot.satellite.map_color)
            self._draw_track(
                painter,
                bounds,
                snapshot.ground_track,
                snapshot.position.observed_at,
                color,
            )
            satellite = self._to_point(
                snapshot.position.latitude_deg,
                snapshot.position.longitude_deg,
                bounds,
            )
            halo = QColor(color)
            halo.setAlpha(75)
            painter.setPen(QPen(halo, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(satellite, 11, 11)
            painter.setPen(QPen(self._theme.current_outline, 2.0))
            painter.setBrush(self._theme.current_fill)
            painter.drawEllipse(satellite, 6, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(satellite, 2.2, 2.2)
            label_offset = QPointF(9, -8)
            if satellite.x() > bounds.right() - 150:
                label_offset = QPointF(-145, -8)
            painter.drawText(
                satellite + label_offset, snapshot.satellite.short_name
            )

        self._draw_satellite_legend(painter, bounds)

        receiver = self._to_point(
            self._location.latitude_deg, self._location.longitude_deg, bounds
        )
        painter.setPen(QPen(self._theme.receiver_text, 1.3))
        painter.setBrush(QColor("#ffb454"))
        painter.drawEllipse(receiver, 5, 5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 180, 84, 85), 2))
        painter.drawEllipse(receiver, 10, 10)
        painter.setPen(self._theme.receiver_text)
        receiver_offset = (
            QPointF(8, 16)
            if receiver.x() < bounds.right() - 35
            else QPointF(-28, 16)
        )
        painter.drawText(receiver + receiver_offset, "RX")

        painter.setClipping(False)
        painter.setPen(QPen(self._theme.border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(bounds)

    def _draw_grid(self, painter: QPainter, bounds: QRectF) -> None:
        painter.setPen(QPen(self._theme.grid, 1, Qt.PenStyle.DotLine))
        for longitude in range(-180, 181, 30):
            x = bounds.left() + (longitude + 180) / 360 * bounds.width()
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        for latitude in range(-60, 61, 30):
            y = bounds.top() + (90 - latitude) / 180 * bounds.height()
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))

    def _draw_land(self, painter: QPainter, bounds: QRectF) -> None:
        path = self._land_path_for(bounds)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.land_fill)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._theme.land_glow, 3.0))
        painter.drawPath(path)
        painter.setPen(QPen(self._theme.land_outline, 0.9))
        painter.drawPath(path)

    def _land_path_for(self, bounds: QRectF) -> QPainterPath:
        key = (bounds.left(), bounds.top(), bounds.width(), bounds.height())
        if self._land_path is not None and self._land_path_key == key:
            return self._land_path
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for polygon in self._land_geometry:
            self._append_polygon(path, polygon, bounds)
        self._land_path = path
        self._land_path_key = key
        return path

    def _append_polygon(
        self, path: QPainterPath, polygon: GeoPolygon, bounds: QRectF
    ) -> None:
        for ring in (polygon.exterior, *polygon.holes):
            first_longitude, first_latitude = ring[0]
            path.moveTo(self._to_point(first_latitude, first_longitude, bounds))
            for longitude, latitude in ring[1:]:
                path.lineTo(self._to_point(latitude, longitude, bounds))
            path.closeSubpath()

    def _draw_coordinate_labels(self, painter: QPainter, bounds: QRectF) -> None:
        painter.setPen(self._theme.coordinate_text)
        for longitude in range(-150, 180, 30):
            x = bounds.left() + (longitude + 180) / 360 * bounds.width()
            suffix = "W" if longitude < 0 else "E"
            label = "0°" if longitude == 0 else f"{abs(longitude)}°{suffix}"
            painter.drawText(
                QRectF(x - 24, bounds.bottom() - 17, 48, 14),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
        for latitude in range(-60, 61, 30):
            y = bounds.top() + (90 - latitude) / 180 * bounds.height()
            suffix = "S" if latitude < 0 else "N"
            label = "0°" if latitude == 0 else f"{abs(latitude)}°{suffix}"
            painter.drawText(
                QRectF(bounds.left() + 4, y - 8, 38, 14),
                Qt.AlignmentFlag.AlignLeft,
                label,
            )

    def _draw_track(
        self,
        painter: QPainter,
        bounds: QRectF,
        track: tuple[SatellitePosition, ...],
        current_time: datetime,
        color: QColor,
    ) -> None:
        if len(track) < 2:
            return
        oldest_time = min(
            (item.observed_at for item in track if item.observed_at <= current_time),
            default=current_time,
        )
        for width, opacity_scale in ((5.0, 0.20), (1.8, 1.0)):
            previous_position = track[0]
            previous_point = self._to_point(
                previous_position.latitude_deg,
                previous_position.longitude_deg,
                bounds,
            )
            for position in track[1:]:
                point = self._to_point(
                    position.latitude_deg, position.longitude_deg, bounds
                )
                if abs(point.x() - previous_point.x()) < bounds.width() / 2:
                    midpoint = previous_position.observed_at + (
                        position.observed_at - previous_position.observed_at
                    ) / 2
                    alpha = historical_track_alpha(
                        midpoint, current_time, oldest_time
                    )
                    segment_color = QColor(color)
                    segment_color.setAlpha(max(5, round(alpha * opacity_scale)))
                    painter.setPen(QPen(segment_color, width))
                    painter.drawLine(previous_point, point)
                previous_position = position
                previous_point = point

        self._draw_track_time_markers(
            painter,
            bounds,
            track,
            current_time,
            oldest_time,
            color,
        )

    def _draw_track_time_markers(
        self,
        painter: QPainter,
        bounds: QRectF,
        track: tuple[SatellitePosition, ...],
        current_time: datetime,
        oldest_time: datetime,
        color: QColor,
    ) -> None:
        for marker in half_hour_track_markers(track):
            point = self._to_point(
                marker.position.latitude_deg,
                marker.position.longitude_deg,
                bounds,
            )
            alpha = historical_track_alpha(
                marker.position.observed_at,
                current_time,
                oldest_time,
                minimum=85,
                maximum=240,
            )
            marker_color = QColor(color)
            marker_color.setAlpha(alpha)
            painter.setPen(QPen(marker_color, 1.3))
            painter.drawLine(point + QPointF(-3.0, -3.0), point + QPointF(3.0, 3.0))
            painter.drawLine(point + QPointF(-3.0, 3.0), point + QPointF(3.0, -3.0))

            metrics = painter.fontMetrics()
            text_width = metrics.horizontalAdvance(marker.label)
            x_offset = 5.0 if point.x() + text_width + 7 < bounds.right() else -text_width - 5.0
            y_offset = -5.0 if point.y() - metrics.height() > bounds.top() else metrics.height()
            label_point = point + QPointF(x_offset, y_offset)
            shadow = QColor(self._theme.label_shadow)
            shadow.setAlpha(min(shadow.alpha(), alpha))
            painter.setPen(QPen(shadow, 3.0))
            painter.drawText(label_point, marker.label)
            painter.setPen(marker_color)
            painter.drawText(label_point, marker.label)

    def _draw_satellite_legend(self, painter: QPainter, bounds: QRectF) -> None:
        if not self._snapshots:
            return
        entries = tuple(
            snapshot.satellite
            for snapshot in sorted(
                self._snapshots.values(),
                key=lambda item: item.satellite.norad_catalog_id,
            )
        )
        legend_height = 8 + len(entries) * 22
        legend = QRectF(
            bounds.left() + 9,
            bounds.bottom() - legend_height - 24,
            112,
            legend_height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.overlay_background)
        painter.drawRoundedRect(legend, 5, 5)
        for index, satellite in enumerate(entries):
            y = legend.top() + 15 + index * 22
            painter.setBrush(self._satellite_color(satellite.map_color))
            painter.drawEllipse(QPointF(legend.left() + 13, y - 4), 5, 5)
            painter.setPen(self._theme.overlay_text)
            painter.drawText(QPointF(legend.left() + 26, y), satellite.short_name)
            painter.setPen(Qt.PenStyle.NoPen)

    def _satellite_color(self, source: str) -> QColor:
        color = QColor(source)
        return color.darker(145) if self._appearance_mode == "normal" else color

    @staticmethod
    def _to_point(latitude: float, longitude: float, bounds: QRectF) -> QPointF:
        return QPointF(
            bounds.left() + (longitude + 180.0) / 360.0 * bounds.width(),
            bounds.top() + (90.0 - latitude) / 180.0 * bounds.height(),
        )

    def _to_geo(self, point: QPointF) -> tuple[float, float]:
        bounds = self._map_bounds()
        longitude = (point.x() - bounds.left()) / bounds.width() * 360.0 - 180.0
        latitude = 90.0 - (point.y() - bounds.top()) / bounds.height() * 180.0
        return max(-90.0, min(90.0, latitude)), max(
            -180.0, min(180.0, longitude)
        )

    def _map_bounds(self) -> QRectF:
        available = QRectF(
            8, 8, max(1, self.width() - 16), max(1, self.height() - 16)
        )
        if available.width() / available.height() > 2.0:
            width = available.height() * 2.0
            return QRectF(
                available.center().x() - width / 2,
                available.top(),
                width,
                available.height(),
            )
        height = available.width() / 2.0
        return QRectF(
            available.left(),
            available.center().y() - height / 2,
            available.width(),
            height,
        )


class Panel(OrbitMapWidget):
    def __init__(self, context: PanelContext) -> None:
        super().__init__()
        self.context = context
        self.set_appearance(context.state.appearance_mode)
        self.set_location(context.state.receiver_location)
        self.location_selected.connect(self._request_location)
        self.satellite_tune_requested.connect(self._request_satellite_tune)
        context.bus.location_updated.connect(self.set_location)
        context.bus.orbit_snapshot.connect(self.set_snapshot)
        context.bus.orbit_removed.connect(self.remove_satellite)
        context.bus.tracking_error.connect(self.set_tracking_error)
        context.bus.tracking_running_changed.connect(self.set_tracking_active)
        context.bus.appearance_changed.connect(self.set_appearance)
        self.set_tracking_active(context.state.tracking_running)
        for snapshot in context.state.orbit_snapshots.values():
            self.set_snapshot(snapshot)
        for catalog_id, message in context.state.tracking_errors.items():
            self.set_tracking_error(catalog_id, message)

    def _request_location(self, latitude: float, longitude: float) -> None:
        self.context.bus.location_requested.emit(ReceiverLocation(latitude, longitude))

    def _request_satellite_tune(self, norad_catalog_id: int) -> None:
        snapshot = self._snapshots.get(norad_catalog_id)
        if snapshot is None:
            return
        receiver_settings = replace(
            self.context.state.receiver_settings,
            center_frequency_hz=snapshot.satellite.lrpt_frequency_hz,
        )
        self.context.bus.receiver_settings_requested.emit(receiver_settings)
