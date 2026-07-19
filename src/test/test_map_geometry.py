from panel_map import load_land_geometry


def test_bundled_natural_earth_geometry_is_detailed_and_cached() -> None:
    geometry = load_land_geometry()
    point_count = sum(
        len(polygon.exterior) + sum(len(hole) for hole in polygon.holes)
        for polygon in geometry
    )

    assert len(geometry) == 127
    assert point_count > 5_000
    assert load_land_geometry() is geometry
    assert all(polygon.exterior[0] == polygon.exterior[-1] for polygon in geometry)
