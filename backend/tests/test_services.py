from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import SnapshotMissingError, SnapshotStore


def test_missing_snapshot_raises(tmp_path: Path):
    store = SnapshotStore(tmp_path / "missing.json.gz")
    assert not store.available
    with pytest.raises(SnapshotMissingError):
        _ = store.snapshot


def test_snapshot_and_indexes_are_cached(snapshot, tmp_path: Path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    store = SnapshotStore(path)
    assert store.snapshot is store.snapshot
    assert store.geocoder is store.geocoder
    assert store.coverage is store.coverage
    assert store.stops is store.stops
    assert store.blockfaces is store.blockfaces


def test_blockfaces_reuse_the_cached_stops(snapshot, tmp_path: Path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    store = SnapshotStore(path)
    assigned = {s.stop_id for b in store.blockfaces for s in b.stops}
    assert assigned == {s.stop_id for s in store.stops}


def test_stops_and_blockfaces_are_rebuilt_after_a_reload(snapshot, tmp_path: Path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    store = SnapshotStore(path)
    first_stops, first_faces = store.stops, store.blockfaces
    store.reload()
    assert store.stops is not first_stops
    assert store.blockfaces is not first_faces


def test_snapshot_reloads_when_the_file_changes(snapshot, tmp_path: Path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    store = SnapshotStore(path)
    assert len(store.snapshot.addresses) == 9
    first_geocoder = store.geocoder

    snapshot.addresses = snapshot.addresses[:5]
    snapshot.save(path)
    mtime = path.stat().st_mtime + 2
    os.utime(path, (mtime, mtime))

    assert len(store.snapshot.addresses) == 5
    assert store.geocoder is not first_geocoder


def test_reload_clears_the_cache(snapshot, tmp_path: Path):
    path = tmp_path / "d.json.gz"
    snapshot.save(path)
    store = SnapshotStore(path)
    first = store.snapshot
    store.reload()
    assert store.snapshot is not first
