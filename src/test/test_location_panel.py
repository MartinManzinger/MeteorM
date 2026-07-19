import pytest

from panel_location import parse_coordinates


@pytest.mark.parametrize(
    ("text", "latitude", "longitude"),
    (
        ("52.520008, 13.404954", 52.520008, 13.404954),
        ("-33.8688; 151.2093", -33.8688, 151.2093),
        (
            "https://www.google.com/maps/@48.858370,2.294481,15z",
            48.858370,
            2.294481,
        ),
        (
            "https://www.google.com/maps/search/?api=1&query=51.5007%2C-0.1246",
            51.5007,
            -0.1246,
        ),
    ),
)
def test_parse_coordinates_accepts_manual_and_google_maps_data(
    text, latitude, longitude
) -> None:
    location = parse_coordinates(text)

    assert location.latitude_deg == pytest.approx(latitude)
    assert location.longitude_deg == pytest.approx(longitude)


@pytest.mark.parametrize("text", ("not a place", "95.0, 13.0", "52.0, 181.0"))
def test_parse_coordinates_rejects_invalid_data(text) -> None:
    with pytest.raises(ValueError):
        parse_coordinates(text)
