from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.blockface import build_blockfaces
from app.osm.boundary import haversine_m, ring_is_closed
from app.osm.snapshot import DistrictSnapshot
from app.stops import GATED_COMPLEX_DOOR_THRESHOLD, build_stops


@dataclass
class CoverageReport:
    district_name: str
    fetched_at: str
    doors: int
    stops: int
    streets: int
    doors_with_unit: int
    multi_unit_stops: int
    gated_complex_candidates: int
    largest_stops: list[dict] = field(default_factory=list)
    cluster_histogram: dict[int, int] = field(default_factory=dict)
    blockfaces: int = 0
    blockfaces_one_side_per_pass: int = 0
    blockfaces_off_network: int = 0
    knock_hours: float = 0.0
    uncapped_knock_hours: float = 0.0
    addresses_missing_street: int = 0
    walkable_ways: dict[str, int] = field(default_factory=dict)
    boundary_rings: int = 0
    boundary_closed_rings: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    extent_km: tuple[float, float] = (0.0, 0.0)
    top_streets: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "district_name": self.district_name,
            "fetched_at": self.fetched_at,
            "doors": self.doors,
            "stops": self.stops,
            "streets": self.streets,
            "doors_with_unit": self.doors_with_unit,
            "multi_unit_stops": self.multi_unit_stops,
            "gated_complex_candidates": self.gated_complex_candidates,
            "largest_stops": self.largest_stops,
            "cluster_histogram": {str(k): v for k, v in sorted(self.cluster_histogram.items())},
            "blockfaces": self.blockfaces,
            "blockfaces_one_side_per_pass": self.blockfaces_one_side_per_pass,
            "blockfaces_off_network": self.blockfaces_off_network,
            "knock_hours": self.knock_hours,
            "uncapped_knock_hours": self.uncapped_knock_hours,
            "addresses_missing_street": self.addresses_missing_street,
            "walkable_ways": dict(sorted(self.walkable_ways.items(), key=lambda kv: -kv[1])),
            "boundary_rings": self.boundary_rings,
            "boundary_closed_rings": self.boundary_closed_rings,
            "bbox": list(self.bbox),
            "extent_km": list(self.extent_km),
            "top_streets": self.top_streets,
        }


def build_coverage_report(snapshot: DistrictSnapshot) -> CoverageReport:
    addresses = snapshot.addresses
    stops = build_stops(addresses)
    blockfaces = build_blockfaces(stops, snapshot.ways)

    sizes = Counter(stop.door_count for stop in stops)
    largest = sorted(stops, key=lambda s: s.door_count, reverse=True)[:8]
    street_doors = Counter(a.street for a in addresses)
    south, west, north, east = snapshot.bbox

    return CoverageReport(
        district_name=snapshot.district_name,
        fetched_at=snapshot.fetched_at,
        doors=len(addresses),
        stops=len(stops),
        streets=len(street_doors),
        doors_with_unit=sum(1 for a in addresses if a.unit),
        multi_unit_stops=sum(1 for stop in stops if stop.door_count > 1),
        gated_complex_candidates=sum(1 for stop in stops if stop.is_gated_candidate),
        largest_stops=[
            {"street": s.street, "number": s.number, "doors": s.door_count} for s in largest
        ],
        cluster_histogram=dict(sizes),
        blockfaces=len(blockfaces),
        blockfaces_one_side_per_pass=sum(1 for b in blockfaces if b.one_side_per_pass),
        blockfaces_off_network=sum(1 for b in blockfaces if b.off_network),
        knock_hours=round(sum(s.dwell_seconds for s in stops) / 3600, 1),
        uncapped_knock_hours=round(sum(s.uncapped_dwell_seconds for s in stops) / 3600, 1),
        addresses_missing_street=sum(1 for a in addresses if not a.street),
        walkable_ways=dict(Counter(w.highway for w in snapshot.ways)),
        boundary_rings=len(snapshot.rings),
        boundary_closed_rings=sum(ring_is_closed(r) for r in snapshot.rings),
        bbox=(south, west, north, east),
        extent_km=(
            round(haversine_m((south, west), (north, west)) / 1000, 1),
            round(haversine_m((south, west), (south, east)) / 1000, 1),
        ),
        top_streets=[
            {"street": street, "doors": doors} for street, doors in street_doors.most_common(10)
        ],
    )


def estimate_effort(
    doors: int,
    session_minutes: int = 180,
    seconds_per_door: int = 75,
    walking_overhead: float = 0.35,
) -> dict:
    """Rough throughput maths, to keep expectations honest."""
    knock_minutes = session_minutes * (1 - walking_overhead)
    doors_per_session = max(1, int(knock_minutes * 60 / seconds_per_door))
    return {
        "session_minutes": session_minutes,
        "seconds_per_door": seconds_per_door,
        "walking_overhead": walking_overhead,
        "doors_per_pair_session": doors_per_session,
        "pair_sessions_for_full_coverage": round(doors / doors_per_session, 1),
    }
