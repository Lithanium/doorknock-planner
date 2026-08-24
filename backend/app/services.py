from __future__ import annotations

import threading
from pathlib import Path

from app.coverage import CoverageReport, build_coverage_report
from app.geocode import LocalGeocoder
from app.osm.snapshot import DistrictSnapshot
from app.walkgraph import EdgeSnap, WalkGraph


class SnapshotMissingError(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"no district snapshot at {path}. Run `make fetch-district` once "
            "to download it, after which the app works entirely offline."
        )
        self.path = path


class SnapshotStore:
    """Loads the cached district snapshot once and derives indexes from it.

    Access is guarded by a lock because FastAPI runs sync endpoints in a
    threadpool, and the snapshot is reloaded when the file on disk changes so
    a `fetch-district --force` takes effect without a server restart.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._snapshot: DistrictSnapshot | None = None
        self._geocoder: LocalGeocoder | None = None
        self._coverage: CoverageReport | None = None
        self._walk_graph: WalkGraph | None = None
        self._address_snaps: dict[str, EdgeSnap] | None = None
        self._loaded_mtime: float | None = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    @property
    def snapshot(self) -> DistrictSnapshot:
        with self._lock:
            return self._snapshot_locked()

    @property
    def geocoder(self) -> LocalGeocoder:
        with self._lock:
            snapshot = self._snapshot_locked()
            if self._geocoder is None:
                self._geocoder = LocalGeocoder(snapshot.addresses)
            return self._geocoder

    @property
    def coverage(self) -> CoverageReport:
        with self._lock:
            snapshot = self._snapshot_locked()
            if self._coverage is None:
                self._coverage = build_coverage_report(snapshot)
            return self._coverage

    @property
    def walk_graph(self) -> WalkGraph:
        with self._lock:
            snapshot = self._snapshot_locked()
            if self._walk_graph is None:
                self._walk_graph = WalkGraph(snapshot.ways)
            return self._walk_graph

    @property
    def address_snaps(self) -> dict[str, EdgeSnap]:
        with self._lock:
            snapshot = self._snapshot_locked()
            if self._walk_graph is None:
                self._walk_graph = WalkGraph(snapshot.ways)
            if self._address_snaps is None:
                self._address_snaps = self._walk_graph.snap_addresses(snapshot.addresses)
            return self._address_snaps

    def reload(self) -> None:
        with self._lock:
            self._clear_locked()

    def _snapshot_locked(self) -> DistrictSnapshot:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            raise SnapshotMissingError(self.path) from None
        if self._snapshot is None or mtime != self._loaded_mtime:
            self._clear_locked()
            self._snapshot = DistrictSnapshot.load(self.path)
            self._loaded_mtime = mtime
        return self._snapshot

    def _clear_locked(self) -> None:
        self._snapshot = None
        self._geocoder = None
        self._coverage = None
        self._walk_graph = None
        self._address_snaps = None
        self._loaded_mtime = None
