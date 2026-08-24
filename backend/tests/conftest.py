from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings
from app.main import create_app
from app.osm.snapshot import Address, DistrictSnapshot, WalkWay

SW = (-37.81, 145.04)
NE = (-37.79, 145.06)

CORNERS = {
    "a": (SW[0], SW[1]),
    "b": (SW[0], NE[1]),
    "c": (NE[0], NE[1]),
    "d": (NE[0], SW[1]),
}

BOUNDARY_WAYS_SHUFFLED = [
    [CORNERS["c"], CORNERS["b"]],
    [CORNERS["a"], CORNERS["b"]],
    [CORNERS["d"], CORNERS["a"]],
    [CORNERS["c"], CORNERS["d"]],
]


def _address(osm_id: str, lat: float, lon: float, number: str, street: str, unit=None) -> Address:
    return Address(osm_id=osm_id, lat=lat, lon=lon, number=number, street=street, unit=unit)


FIXTURE_ADDRESSES = [
    _address("n1", -37.800, 145.050, "22", "Yerrin Street"),
    _address("n2", -37.8005, 145.0502, "24", "Yerrin Street"),
    _address("n3", -37.8010, 145.0504, "26", "Yerrin Street"),
    _address("n4", -37.8020, 145.0510, "14", "Brenbeal Street", unit="1"),
    _address("n5", -37.8020, 145.0511, "14", "Brenbeal Street", unit="2"),
    _address("n6", -37.8021, 145.0511, "14", "Brenbeal Street", unit="3"),
    _address("n7", -37.8030, 145.0520, "99", "High Street South"),
    _address("n8", -37.8040, 145.0530, "101", "Mountain View Road"),
    _address("n9", -37.7700, 145.0550, "5", "Outside Avenue"),
]

FIXTURE_WAYS = [
    WalkWay(
        osm_id=1,
        geometry=[(-37.800, 145.050), (-37.801, 145.0505)],
        tags={"highway": "residential", "name": "Yerrin Street"},
    ),
    WalkWay(
        osm_id=2,
        geometry=[(-37.803, 145.052), (-37.804, 145.053)],
        tags={"highway": "primary", "name": "High Street South", "lanes": "4"},
    ),
    WalkWay(
        osm_id=3,
        geometry=[(-37.802, 145.051), (-37.8025, 145.0515)],
        tags={"highway": "footway"},
    ),
]


@pytest.fixture
def snapshot() -> DistrictSnapshot:
    from app.osm.boundary import assemble_rings

    return DistrictSnapshot(
        district_name="Electoral district of Testville",
        relation_id=999,
        fetched_at="2026-08-23T00:00:00+00:00",
        rings=assemble_rings(BOUNDARY_WAYS_SHUFFLED),
        addresses=list(FIXTURE_ADDRESSES),
        ways=list(FIXTURE_WAYS),
    )


@pytest.fixture
def snapshot_path(snapshot: DistrictSnapshot, tmp_path: Path) -> Path:
    path = tmp_path / "testville.json.gz"
    snapshot.save(path)
    return path


@pytest.fixture
def client(snapshot_path: Path):
    from fastapi.testclient import TestClient

    return TestClient(create_app(snapshot_path=snapshot_path))


@pytest.fixture
def empty_client(tmp_path: Path):
    from fastapi.testclient import TestClient

    return TestClient(create_app(snapshot_path=tmp_path / "missing.json.gz"))


@pytest.fixture
def real_snapshot() -> DistrictSnapshot:
    path = load_settings().snapshot_path
    if not path.exists():
        pytest.skip(f"no cached district snapshot at {path}; run `make fetch-district`")
    return DistrictSnapshot.load(path)
