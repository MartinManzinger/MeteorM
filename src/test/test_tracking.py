from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

import pytest

from gui import (
    METEOR_M_N2_4,
    NOAA_15,
    NOAA_18,
    NOAA_19,
    TRACKED_SATELLITES,
    TLERecord,
)
from main import (
    CelestrakTLEProvider,
    SatelliteTracker,
    SatelliteTrackingService,
    SettingsStore,
    TLEProvider,
    TLEProviderError,
    make_tracking_services,
)


LINE1 = "1 59051U 24039A   26200.25935704 -.00000017  00000+0  12162-4 0  9997"
LINE2 = "2 59051  98.7028 159.1575 0008247  78.7163 281.4941 14.22432337123828"
TLE_TEXT = f"METEOR-M2 4\n{LINE1}\n{LINE2}\n".encode("ascii")
RETRIEVED_AT = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.payload[:size]


class StaticProvider(TLEProvider):
    def __init__(self, record: TLERecord) -> None:
        self.record = record
        self.calls: list[bool] = []
        self.cache_calls = 0

    def get_cached_tle(self, satellite) -> TLERecord | None:
        assert satellite == self.record.satellite
        self.cache_calls += 1
        return self.record

    def get_tle(self, satellite, *, force_refresh=False) -> TLERecord:
        assert satellite == self.record.satellite
        self.calls.append(force_refresh)
        return self.record


class EmptyCacheProvider(TLEProvider):
    def __init__(self, record: TLERecord) -> None:
        self.record = record
        self.calls: list[bool] = []

    def get_cached_tle(self, satellite) -> TLERecord | None:
        assert satellite == self.record.satellite
        return None

    def get_tle(self, satellite, *, force_refresh=False) -> TLERecord:
        assert satellite == self.record.satellite
        self.calls.append(force_refresh)
        return self.record


@pytest.fixture
def tle_record() -> TLERecord:
    return TLERecord(
        satellite=METEOR_M_N2_4,
        name="METEOR-M2 4",
        line1=LINE1,
        line2=LINE2,
        retrieved_at=RETRIEVED_AT,
        source_url="https://example.test/tle",
    )


def test_tracking_service_catalog_includes_meteor_and_noaa(tmp_path) -> None:
    services = make_tracking_services(SettingsStore(tmp_path / "settings.yaml"))

    assert set(services) == {
        satellite.norad_catalog_id for satellite in TRACKED_SATELLITES
    }
    assert {service.satellite for service in services.values()} == set(
        TRACKED_SATELLITES
    )


def test_noaa_catalog_uses_legacy_apt_tune_frequencies() -> None:
    assert {
        satellite.norad_catalog_id: satellite.receiver_frequency_hz
        for satellite in (NOAA_15, NOAA_18, NOAA_19)
    } == {
        25338: 137_620_000.0,
        28654: 137_912_500.0,
        33591: 137_100_000.0,
    }
    assert {satellite.receiver_mode for satellite in (NOAA_15, NOAA_18, NOAA_19)} == {
        "APT"
    }


def test_celestrak_provider_downloads_validates_and_reuses_cache(tmp_path) -> None:
    calls = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(TLE_TEXT)

    cache_path = tmp_path / "settings.yaml"
    cache = SettingsStore(cache_path)
    provider = CelestrakTLEProvider(
        cache, opener=opener, clock=lambda: RETRIEVED_AT
    )
    downloaded = provider.get_tle(METEOR_M_N2_4)

    assert not downloaded.from_cache
    assert "CATNR=59051" in calls[0][0]
    assert cache.load_tle(59051)["line1"] == LINE1

    def unexpected_network_call(*args, **kwargs):
        raise AssertionError("fresh cache should prevent a network call")

    cached_provider = CelestrakTLEProvider(
        cache,
        opener=unexpected_network_call,
        clock=lambda: RETRIEVED_AT + timedelta(hours=1),
    )
    cached = cached_provider.get_tle(METEOR_M_N2_4)

    assert cached.from_cache
    assert cached.line1 == LINE1
    assert cached_provider.get_tle(METEOR_M_N2_4, force_refresh=True).from_cache


def test_celestrak_provider_falls_back_to_stale_valid_cache(tmp_path) -> None:
    cache = SettingsStore(tmp_path / "settings.yaml")
    initial = CelestrakTLEProvider(
        cache,
        opener=lambda *args, **kwargs: FakeResponse(TLE_TEXT),
        clock=lambda: RETRIEVED_AT,
    )
    initial.get_tle(METEOR_M_N2_4)

    def offline(*args, **kwargs):
        raise URLError("offline")

    offline_provider = CelestrakTLEProvider(
        cache,
        opener=offline,
        clock=lambda: RETRIEVED_AT + timedelta(hours=7),
    )

    assert offline_provider.get_tle(METEOR_M_N2_4).from_cache


def test_celestrak_provider_rejects_bad_checksum(tmp_path) -> None:
    bad_payload = TLE_TEXT[:-2] + b"9\n"
    cache = SettingsStore(tmp_path / "settings.yaml")
    provider = CelestrakTLEProvider(
        cache,
        opener=lambda *args, **kwargs: FakeResponse(bad_payload),
        clock=lambda: RETRIEVED_AT,
    )

    with pytest.raises(TLEProviderError, match="checksum"):
        provider.get_tle(METEOR_M_N2_4)


def test_satellite_tracker_propagates_position_and_ground_track(tle_record) -> None:
    tracker = SatelliteTracker(tle_record)
    observed_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    position = tracker.position_at(observed_at)
    track = tracker.ground_track(
        observed_at,
        duration=timedelta(minutes=6),
        step=timedelta(minutes=2),
    )

    assert position.latitude_deg == pytest.approx(27.8114, abs=0.001)
    assert position.longitude_deg == pytest.approx(-133.2960, abs=0.001)
    assert position.altitude_km == pytest.approx(818.9, abs=0.1)
    assert len(track) == 4
    assert track[0] == position
    assert track[-1].observed_at == observed_at + timedelta(minutes=6)


def test_tracking_service_publishes_from_worker_and_stops(tle_record) -> None:
    provider = StaticProvider(tle_record)
    service = SatelliteTrackingService(
        provider,
        METEOR_M_N2_4,
        update_interval=0.02,
        track_refresh_interval=1,
        queue_size=1,
    )
    service.start()
    deadline = time.monotonic() + 2
    snapshot = None
    while snapshot is None and time.monotonic() < deadline:
        time.sleep(0.02)
        snapshot = service.take_latest()
    service.request_tle_refresh()
    while len(provider.calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    service.stop()

    assert snapshot is not None
    assert snapshot.satellite == METEOR_M_N2_4
    assert len(snapshot.ground_track) == 901
    assert (
        snapshot.position.observed_at - snapshot.ground_track[0].observed_at
    ).total_seconds() == pytest.approx(3600.0, abs=1.0)
    assert (
        snapshot.ground_track[-1].observed_at - snapshot.position.observed_at
    ).total_seconds() == pytest.approx(7200.0, abs=1.0)
    assert service.last_error is None
    assert not service.running
    assert provider.cache_calls == 1
    assert provider.calls == [True]


def test_tracking_service_waits_for_manual_fetch_when_cache_is_empty(
    tle_record,
) -> None:
    provider = EmptyCacheProvider(tle_record)
    service = SatelliteTrackingService(
        provider,
        METEOR_M_N2_4,
        update_interval=0.02,
        track_refresh_interval=1,
    )
    service.start()
    time.sleep(0.05)

    assert provider.calls == []
    assert "No cached orbital elements" in service.last_error

    service.request_tle_refresh()
    deadline = time.monotonic() + 2
    snapshot = None
    while snapshot is None and time.monotonic() < deadline:
        time.sleep(0.02)
        snapshot = service.take_latest()
    service.stop()

    assert snapshot is not None
    assert provider.calls == [True]
