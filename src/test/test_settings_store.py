from gui import NOAA_15, ReceiverLocation, ReceiverSettings
from main import SettingsStore


def test_settings_store_persists_all_values_in_one_yaml_file(tmp_path) -> None:
    path = tmp_path / "settings.yaml"
    store = SettingsStore(path)
    assert store.appearance_mode() == "green"
    receiver = ReceiverSettings(
        center_frequency_hz=137_100_000.0,
        sample_rate_hz=3_000_000.0,
        filter_bandwidth_hz=2_000_000.0,
        lna_gain_db=32,
        vga_gain_db=30,
        amplifier_enabled=True,
        device="Test receiver",
    )
    location = ReceiverLocation(48.1, 11.5)

    store.update_receiver(receiver)
    store.update_location(location)
    store.update_log_verbosity("DEBUG")
    store.update_appearance_mode("normal")
    store.update_enabled_satellites({57166, NOAA_15.norad_catalog_id})
    store.save_tle(59051, {"name": "METEOR-M2 4", "line1": "one", "line2": "two"})

    reloaded = SettingsStore(path)
    assert reloaded.receiver_settings() == receiver
    assert reloaded.receiver_location() == location
    assert reloaded.log_verbosity() == "DEBUG"
    assert reloaded.appearance_mode() == "normal"
    assert reloaded.enabled_satellite_ids() == {57166, NOAA_15.norad_catalog_id}
    assert reloaded.load_tle(59051)["line2"] == "two"
    assert not list(tmp_path.glob("*.tmp"))


def test_sample_rate_is_normalized_to_safe_whole_mhz_steps(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.yaml")
    store.update_receiver(ReceiverSettings(sample_rate_hz=2_600_000.0))

    assert SettingsStore(store.path).receiver_settings().sample_rate_hz == 3_000_000.0

    store.update_receiver(ReceiverSettings(sample_rate_hz=25_000_000.0))
    assert SettingsStore(store.path).receiver_settings().sample_rate_hz == 20_000_000.0
