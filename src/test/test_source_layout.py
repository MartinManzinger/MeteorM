from pathlib import Path


def test_production_python_files_match_the_intended_flat_architecture() -> None:
    source = Path(__file__).resolve().parent.parent
    expected = {
        "main.py",
        "gui.py",
        "panel_map.py",
        "panel_log.py",
        "panel_location.py",
        "panel_satellite_tracking.py",
        "panel_sdr.py",
        "panel_spectrum.py",
        "panel_constellation.py",
        "panel_picture.py",
    }

    assert {path.name for path in source.glob("*.py")} == expected
