from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.osm.boundary import Point, Ring, point_in_rings, rings_bbox

SNAPSHOT_FORMAT = 1

WAY_TAG_WHITELIST = (
    "highway",
    "name",
    "foot",
    "access",
    "sidewalk",
    "footway",
    "width",
    "lanes",
    "maxspeed",
    "surface",
    "service",
    "tunnel",
    "bridge",
    "crossing",
    "barrier",
    "dual_carriageway",
    "junction",
)


@dataclass(frozen=True, slots=True)
class Address:
    osm_id: str
    lat: float
    lon: float
    number: str
    street: str
    unit: str | None = None
    postcode: str | None = None

    @property
    def point(self) -> Point:
        return (self.lat, self.lon)

    @property
    def label(self) -> str:
        base = f"{self.number} {self.street}"
        return f"{self.unit}/{base}" if self.unit else base

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.osm_id,
            "lat": self.lat,
            "lon": self.lon,
            "number": self.number,
            "street": self.street,
        }
        if self.unit:
            d["unit"] = self.unit
        if self.postcode:
            d["postcode"] = self.postcode
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Address:
        return cls(
            osm_id=d["id"],
            lat=d["lat"],
            lon=d["lon"],
            number=d["number"],
            street=d["street"],
            unit=d.get("unit"),
            postcode=d.get("postcode"),
        )


@dataclass(frozen=True, slots=True)
class WalkWay:
    osm_id: int
    geometry: list[Point]
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def highway(self) -> str:
        return self.tags.get("highway", "")

    @property
    def name(self) -> str | None:
        return self.tags.get("name")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.osm_id,
            "tags": self.tags,
            "geometry": [[lat, lon] for lat, lon in self.geometry],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WalkWay:
        return cls(
            osm_id=d["id"],
            tags=d.get("tags", {}),
            geometry=[(p[0], p[1]) for p in d["geometry"]],
        )


@dataclass
class DistrictSnapshot:
    district_name: str
    relation_id: int
    fetched_at: str
    rings: list[Ring]
    addresses: list[Address]
    ways: list[WalkWay]
    boundary_source: str = "osm"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return rings_bbox(self.rings)

    def contains(self, point: Point) -> bool:
        return point_in_rings(point, self.rings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": SNAPSHOT_FORMAT,
            "meta": {
                "district_name": self.district_name,
                "relation_id": self.relation_id,
                "fetched_at": self.fetched_at,
                "boundary_source": self.boundary_source,
            },
            "boundary": {"rings": [[[lat, lon] for lat, lon in r] for r in self.rings]},
            "addresses": [a.to_dict() for a in self.addresses],
            "ways": [w.to_dict() for w in self.ways],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DistrictSnapshot:
        fmt = d.get("format")
        if fmt != SNAPSHOT_FORMAT:
            raise ValueError(f"unsupported snapshot format {fmt!r}; re-run `make fetch-district`")
        meta = d["meta"]
        return cls(
            district_name=meta["district_name"],
            relation_id=meta["relation_id"],
            fetched_at=meta["fetched_at"],
            boundary_source=meta.get("boundary_source", "osm"),
            rings=[[(p[0], p[1]) for p in ring] for ring in d["boundary"]["rings"]],
            addresses=[Address.from_dict(a) for a in d["addresses"]],
            ways=[WalkWay.from_dict(w) for w in d["ways"]],
        )

    def save(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), separators=(",", ":")).encode()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as fh:
            fh.write(payload)
        tmp.replace(path)
        return path.stat().st_size

    @classmethod
    def load(cls, path: Path) -> DistrictSnapshot:
        with gzip.open(path, "rb") as fh:
            return cls.from_dict(json.loads(fh.read().decode()))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
