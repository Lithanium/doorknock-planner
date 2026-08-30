from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.blockface import Blockface
from app.geocode import normalise_street
from app.osm.boundary import Point, haversine_m
from app.stops import sort_key

DEFAULT_TARGET_DOORS = 100
MIN_TARGET_DOORS = 20
MAX_TARGET_DOORS = 2_000

ZONE_ADJACENCY_M = 250.0
"""Centroid distance under which two blockfaces count as walk-neighbours.

Matches `territory.UNIT_ADJACENCY_M`. Blockfaces mostly meet through shared
span nodes; this covers the off-network fallbacks, which have no geometry to
share.
"""


@dataclass(frozen=True, slots=True)
class Zone:
    """A connected patch of the electorate holding roughly a set number of doors.

    Cut geometrically, so the shape is close to a rectangle, but the cuts land
    between whole blockfaces rather than through them - a run of houses a pair
    walks in one go is never divided between two zones.
    """

    zone_id: str
    blockfaces: tuple[Blockface, ...]
    dropped_blockfaces: tuple[Blockface, ...] = ()

    @property
    def door_count(self) -> int:
        return sum(b.door_count for b in self.blockfaces)

    @property
    def stop_count(self) -> int:
        return sum(b.stop_count for b in self.blockfaces)

    @property
    def minutes(self) -> float:
        return sum(b.minutes for b in self.blockfaces)

    @property
    def dropped_doors(self) -> int:
        return sum(b.door_count for b in self.dropped_blockfaces)

    @property
    def streets(self) -> list[str]:
        seen: dict[str, None] = {}
        for face in sorted(self.blockfaces, key=lambda b: (b.street, sort_key(b.number_range[0]))):
            seen.setdefault(face.street)
        return list(seen)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(south, west, north, east) around the zone's stops."""
        lats = [s.lat for b in self.blockfaces for s in b.stops]
        lons = [s.lon for b in self.blockfaces for s in b.stops]
        return (min(lats), min(lons), max(lats), max(lons))

    @property
    def centroid(self) -> Point:
        points = [s.point for b in self.blockfaces for s in b.stops]
        return (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )

    @property
    def label(self) -> str:
        streets = self.streets
        head = ", ".join(streets[:2])
        if len(streets) > 2:
            head += f" +{len(streets) - 2} more"
        return f"{head} - {self.door_count} doors"

    def to_dict(self) -> dict:
        south, west, north, east = self.bbox
        return {
            "id": self.zone_id,
            "label": self.label,
            "doors": self.door_count,
            "stops": self.stop_count,
            "blockfaces": len(self.blockfaces),
            "streets": self.streets,
            "minutes": round(self.minutes, 1),
            "bbox": [south, west, north, east],
            "dropped_doors": self.dropped_doors,
        }


@dataclass
class ZonePlan:
    zones: list[Zone]
    target_doors: int
    total_doors: int
    dropped_blockfaces: list[Blockface] = field(default_factory=list)
    split_streets: list[str] = field(default_factory=list)

    @property
    def covered_doors(self) -> int:
        return sum(z.door_count for z in self.zones)

    @property
    def dropped_doors(self) -> int:
        return sum(b.door_count for b in self.dropped_blockfaces)

    @property
    def coverage_pct(self) -> float:
        return 0.0 if not self.total_doors else self.covered_doors / self.total_doors

    @property
    def size_spread_pct(self) -> float:
        """How far the largest and smallest zones sit apart, against the mean."""
        doors = [z.door_count for z in self.zones]
        if len(doors) < 2:
            return 0.0
        mean = sum(doors) / len(doors)
        return 0.0 if mean == 0 else (max(doors) - min(doors)) / mean

    def to_dict(self) -> dict:
        return {
            "target_doors": self.target_doors,
            "zone_count": len(self.zones),
            "total_doors": self.total_doors,
            "covered_doors": self.covered_doors,
            "dropped_doors": self.dropped_doors,
            "dropped_blockfaces": len(self.dropped_blockfaces),
            "coverage_pct": round(self.coverage_pct * 100, 1),
            "size_spread_pct": round(self.size_spread_pct * 100, 1),
            "split_streets": self.split_streets,
        }


def build_zones(blockfaces: list[Blockface], target_doors: int) -> ZonePlan:
    """Cuts the whole electorate into connected zones of about `target_doors`.

    Recursive median splits on the longer axis, the way a KD-tree divides a
    plane, so zones come out close to rectangular and easy to read off a map.
    Each cut falls between whole blockfaces, so a run of houses is never
    divided. Any part of a zone that turns out to be unreachable from the rest
    of it on foot - the far side of the Eastern Freeway, say - is dropped and
    counted rather than quietly left in.
    """
    if target_doors < 1:
        raise ValueError("target_doors must be at least 1")
    usable = [b for b in blockfaces if b.stops]
    total = sum(b.door_count for b in usable)

    zones: list[Zone] = []
    dropped: list[Blockface] = []
    for index, group in enumerate(_split_to_target(usable, target_doors)):
        kept, strays = _largest_connected_component(group)
        dropped.extend(strays)
        if kept:
            zones.append(
                Zone(
                    zone_id=f"z{index + 1:04d}",
                    blockfaces=tuple(
                        sorted(kept, key=lambda b: (b.street, sort_key(b.number_range[0])))
                    ),
                    dropped_blockfaces=tuple(strays),
                )
            )
    zones.sort(key=lambda z: (z.centroid[0], z.centroid[1]))
    zones = [
        Zone(
            zone_id=f"z{index + 1:04d}",
            blockfaces=z.blockfaces,
            dropped_blockfaces=z.dropped_blockfaces,
        )
        for index, z in enumerate(zones)
    ]
    return ZonePlan(
        zones=zones,
        target_doors=target_doors,
        total_doors=total,
        dropped_blockfaces=dropped,
        split_streets=_streets_spanning_zones(zones),
    )


def _split_to_target(
    blockfaces: list[Blockface], target_doors: int
) -> list[list[Blockface]]:
    """Recursive proportional bisection until each part is about one target.

    Splitting into halves would only ever produce powers of two, so each cut
    is proportional: a group worth 7 zones splits 3 : 4, and every leaf lands
    near the target instead of the leaves nearest the root being twice the size
    of the rest.
    """
    doors = sum(b.door_count for b in blockfaces)
    parts = max(1, round(doors / target_doors))
    if parts <= 1 or len(blockfaces) <= 1:
        return [blockfaces]

    left_parts = parts // 2
    axis = _longer_axis(blockfaces)
    ordered = sorted(blockfaces, key=lambda b: b.centroid[axis])
    cut_doors = doors * left_parts / parts

    running = 0
    cut = 0
    for index, face in enumerate(ordered):
        if running >= cut_doors:
            break
        running += face.door_count
        cut = index + 1
    cut = min(max(cut, 1), len(ordered) - 1)

    return _split_to_target(ordered[:cut], target_doors) + _split_to_target(
        ordered[cut:], target_doors
    )


def _longer_axis(blockfaces: list[Blockface]) -> int:
    """0 for latitude, 1 for longitude: whichever the group is wider along.

    Compared in metres, not degrees. A degree of longitude is only ~790 m at
    Melbourne's latitude against ~111 km for latitude, so comparing raw degrees
    would cut nearly every group the same way and produce long thin slivers.
    """
    lats = [b.centroid[0] for b in blockfaces]
    lons = [b.centroid[1] for b in blockfaces]
    mid_lat = (min(lats) + max(lats)) / 2
    height_m = (max(lats) - min(lats)) * 111_320.0
    width_m = (max(lons) - min(lons)) * 111_320.0 * math.cos(math.radians(mid_lat))
    return 0 if height_m >= width_m else 1


def _largest_connected_component(
    blockfaces: list[Blockface],
) -> tuple[list[Blockface], list[Blockface]]:
    """Splits a zone into its biggest walkable piece and everything stranded."""
    if len(blockfaces) <= 1:
        return list(blockfaces), []
    adjacency = _adjacency(blockfaces)
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in range(len(blockfaces)):
        if start in seen:
            continue
        stack, component = [start], []
        seen.add(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    if len(components) == 1:
        return list(blockfaces), []
    # Biggest by doors, not by blockface count: a zone should keep the part
    # that holds the work, not the part with the most fragments in it.
    components.sort(key=lambda c: sum(blockfaces[i].door_count for i in c), reverse=True)
    keep = {i for i in components[0]}
    return (
        [b for i, b in enumerate(blockfaces) if i in keep],
        [b for i, b in enumerate(blockfaces) if i not in keep],
    )


def _adjacency(blockfaces: list[Blockface]) -> list[set[int]]:
    """Which blockfaces touch: a shared span node, or near-coincident centroids."""
    adjacency: list[set[int]] = [set() for _ in blockfaces]
    by_node: dict[tuple[float, float], list[int]] = defaultdict(list)
    for index, face in enumerate(blockfaces):
        for node in face.network_nodes:
            by_node[node].append(index)
    for indexes in by_node.values():
        for a in indexes:
            for b in indexes:
                if a != b:
                    adjacency[a].add(b)
    for a in range(len(blockfaces)):
        for b in range(a + 1, len(blockfaces)):
            if b in adjacency[a]:
                continue
            if haversine_m(blockfaces[a].centroid, blockfaces[b].centroid) <= ZONE_ADJACENCY_M:
                adjacency[a].add(b)
                adjacency[b].add(a)
    return adjacency


PALETTE_SIZE = 8
"""How many colours the map cycles through for zones.

Measured on the district: a greedy colouring needs only 5-6 at every offered
target, so eight leaves headroom while keeping every colour far enough from the
others to tell apart. Twelve forced muddy olives and a second red into the set.
"""


def palette_indexes(zones: list[Zone], palette_size: int = PALETTE_SIZE) -> dict[str, int]:
    """Assigns each zone a colour slot so touching zones never share one.

    Greedy graph colouring over overlapping bounding boxes. With ~286 zones and
    12 colours a plain `index % 12` would repeatedly put the same colour either
    side of a boundary, which is exactly where the eye needs the contrast.
    """
    boxes = [z.bbox for z in zones]
    colours: dict[str, int] = {}
    for index, zone in enumerate(zones):
        taken = {
            colours[zones[other].zone_id]
            for other in range(index)
            if _boxes_touch(boxes[index], boxes[other])
        }
        colours[zone.zone_id] = next(
            (c for c in range(palette_size) if c not in taken), index % palette_size
        )
    return colours


def _boxes_touch(a: tuple[float, ...], b: tuple[float, ...], pad: float = 0.0015) -> bool:
    """Whether two bounding boxes overlap, padded by roughly 150 m."""
    return not (
        a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1]
    )


def _streets_spanning_zones(zones: list[Zone]) -> list[str]:
    """Streets that ended up in more than one zone.

    Unavoidable rather than a fault: at a 100-door target no zone can hold a
    150-door street, so the honest thing is to report which streets it happened
    to instead of implying every street stayed whole.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    for zone in zones:
        for face in zone.blockfaces:
            seen[normalise_street(face.street)].add(zone.zone_id)
    names: dict[str, str] = {}
    for zone in zones:
        for face in zone.blockfaces:
            names.setdefault(normalise_street(face.street), face.street)
    return sorted(names[key] for key, ids in seen.items() if len(ids) > 1)
