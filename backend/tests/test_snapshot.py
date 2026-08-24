from __future__ import annotations

import gzip
import json

import pytest

from app.osm.snapshot import DistrictSnapshot


def test_round_trips_through_gzip(snapshot, tmp_path):
    path = tmp_path / "d.json.gz"
    size = snapshot.save(path)
    assert size > 0
    loaded = DistrictSnapshot.load(path)
    assert loaded.district_name == snapshot.district_name
    assert loaded.fetched_at == snapshot.fetched_at
    assert loaded.addresses == snapshot.addresses
    assert loaded.ways == snapshot.ways
    assert loaded.rings == snapshot.rings


def test_save_is_atomic_and_leaves_no_temp_file(snapshot, tmp_path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    assert [p.name for p in tmp_path.iterdir()] == ["d.json.gz"]


def test_rejects_an_unknown_snapshot_format(snapshot, tmp_path):
    path = tmp_path / "d.json.gz"
    payload = snapshot.to_dict() | {"format": 99}
    with gzip.open(path, "wb") as fh:
        fh.write(json.dumps(payload).encode())
    with pytest.raises(ValueError, match="fetch-district"):
        DistrictSnapshot.load(path)


def test_contains_uses_the_district_polygon(snapshot):
    assert snapshot.contains((-37.800, 145.050)) is True
    assert snapshot.contains((-37.770, 145.055)) is False


def test_bbox_covers_all_boundary_points(snapshot):
    south, west, north, east = snapshot.bbox
    assert south == pytest.approx(-37.81)
    assert north == pytest.approx(-37.79)
    assert west == pytest.approx(145.04)
    assert east == pytest.approx(145.06)


def test_address_label_formats_units():
    from app.osm.snapshot import Address

    plain = Address(osm_id="n1", lat=0, lon=0, number="22", street="Yerrin Street")
    unit = Address(osm_id="n2", lat=0, lon=0, number="14", street="Brenbeal Street", unit="3")
    assert plain.label == "22 Yerrin Street"
    assert unit.label == "3/14 Brenbeal Street"


def test_optional_address_fields_are_omitted_when_empty():
    from app.osm.snapshot import Address

    d = Address(osm_id="n1", lat=0, lon=0, number="22", street="Yerrin Street").to_dict()
    assert "unit" not in d and "postcode" not in d
