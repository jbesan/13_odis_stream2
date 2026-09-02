"""Unit tests for utility functions in app/utils/common.py."""

import pytest
from utils.common import project_point, normalize_text


def test_normalize_text():
    assert normalize_text("Éléphant") == "elephant"
    assert normalize_text("Paris 1er") == "paris 1er"
    assert normalize_text(123) == "123"


def test_project_point_identity():
    # Same CRS returns identical coordinates
    lon, lat = project_point(2.35, 48.85, from_crs="EPSG:4326", to_crs="EPSG:4326")
    assert lon == 2.35
    assert lat == 48.85


def test_project_point_lambert93_to_wgs84():
    # Paris Notre-Dame: X=652200, Y=6861900 in Lambert-93
    # Approximate WGS84: Lon=2.35, Lat=48.85
    lon, lat = project_point(652200.0, 6861900.0, from_crs="EPSG:2154", to_crs="EPSG:4326")
    assert pytest.approx(lon, abs=0.01) == 2.35
    assert pytest.approx(lat, abs=0.01) == 48.85


def test_project_point_wgs84_to_lambert93_roundtrip():
    original_lon, original_lat = 2.3458, 48.8562
    # Forward: WGS84 -> L93
    x, y = project_point(original_lon, original_lat, from_crs="EPSG:4326", to_crs="EPSG:2154")
    # Inverse: L93 -> WGS84
    roundtrip_lon, roundtrip_lat = project_point(x, y, from_crs="EPSG:2154", to_crs="EPSG:4326")

    assert pytest.approx(roundtrip_lon, abs=1e-7) == original_lon
    assert pytest.approx(roundtrip_lat, abs=1e-7) == original_lat


def test_project_point_unsupported_crs():
    with pytest.raises(ValueError, match="Unsupported CRS transformation"):
        project_point(0.0, 0.0, from_crs="EPSG:3857", to_crs="EPSG:4326")
