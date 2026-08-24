from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.osm.boundary import haversine_m, ring_is_closed
from app.osm.snapshot import DistrictSnapshot

GATED_COMPLEX_DOOR_THRESHOLD = 8
"""A street number with this many doors is likely a gated block needing a human call."""


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
    clusters: dict[tuple[str, str], list] = defaultdict(list)
    for address in addresses:
        clusters[(address.street, address.number)].append(address)

    sizes = Counter(len(group) for group in clusters.values())
    largest = sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:8]
    street_doors = Counter(a.street for a in addresses)
    south, west, north, east = snapshot.bbox

    return CoverageReport(
        district_name=snapshot.district_name,
        fetched_at=snapshot.fetched_at,
        doors=len(addresses),
        stops=len(clusters),
        streets=len(street_doors),
        doors_with_unit=sum(1 for a in addresses if a.unit),
        multi_unit_stops=sum(1 for group in clusters.values() if len(group) > 1),
        gated_complex_candidates=sum(
            1 for group in clusters.values() if len(group) >= GATED_COMPLEX_DOOR_THRESHOLD
        ),
        largest_stops=[
            {"street": street, "number": number, "doors": len(group)}
            for (street, number), group in largest
        ],
        cluster_histogram=dict(sizes),
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
