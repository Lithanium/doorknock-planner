from __future__ import annotations

from functools import cached_property
from pathlib import Path

from app.coverage import CoverageReport, build_coverage_report
from app.geocode import LocalGeocoder
from app.osm.snapshot import DistrictSnapshot


class SnapshotMissingError(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"no district snapshot at {path}. Run `make fetch-district` once "
            "to download it, after which the app works entirely offline."
        )
        self.path = path


class SnapshotStore:
    """Loads the cached district snapshot once and derives indexes from it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def available(self) -> bool:
        return self.path.exists()

    @cached_property
    def snapshot(self) -> DistrictSnapshot:
        if not self.available:
            raise SnapshotMissingError(self.path)
        return DistrictSnapshot.load(self.path)

    @cached_property
    def geocoder(self) -> LocalGeocoder:
        return LocalGeocoder(self.snapshot.addresses)

    @cached_property
    def coverage(self) -> CoverageReport:
        return build_coverage_report(self.snapshot)

    def reload(self) -> None:
        for attr in ("snapshot", "geocoder", "coverage"):
            self.__dict__.pop(attr, None)
