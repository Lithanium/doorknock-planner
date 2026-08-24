from __future__ import annotations

import math
from collections import defaultdict

Point = tuple[float, float]
"""A (lat, lon) pair."""

Ring = list[Point]

_KEY_PRECISION = 7


def _key(point: Point) -> tuple[float, float]:
    return (round(point[0], _KEY_PRECISION), round(point[1], _KEY_PRECISION))


def assemble_rings(ways: list[list[Point]]) -> list[Ring]:
    """Stitch unordered, arbitrarily-directed boundary ways into closed rings.

    An OSM boundary relation is a bag of way members with no guaranteed order
    or winding, so they must be joined end-to-end before the boundary can be
    used as a polygon.
    """
    segments = [list(w) for w in ways if len(w) >= 2]
    endpoints: dict[tuple[float, float], list[int]] = defaultdict(list)
    for index, segment in enumerate(segments):
        endpoints[_key(segment[0])].append(index)
        endpoints[_key(segment[-1])].append(index)

    used: set[int] = set()
    rings: list[Ring] = []

    for start in range(len(segments)):
        if start in used:
            continue
        used.add(start)
        ring: Ring = list(segments[start])
        while True:
            tail = _key(ring[-1])
            if tail == _key(ring[0]) and len(ring) > 2:
                break
            nxt = next((i for i in endpoints.get(tail, ()) if i not in used), None)
            if nxt is None:
                break
            used.add(nxt)
            segment = segments[nxt]
            ring.extend(segment[1:] if _key(segment[0]) == tail else reversed(segment[:-1]))
        rings.append(ring)

    return rings


def ring_is_closed(ring: Ring, tolerance_m: float = 1.0) -> bool:
    return len(ring) > 3 and haversine_m(ring[0], ring[-1]) <= tolerance_m


def point_in_ring(point: Point, ring: Ring) -> bool:
    """Ray-casting test. Treats the ring as implicitly closed."""
    lat, lon = point
    inside = False
    count = len(ring)
    if count < 3:
        return False
    for i in range(count):
        a_lat, a_lon = ring[i]
        b_lat, b_lon = ring[(i + 1) % count]
        if (a_lat > lat) != (b_lat > lat):
            crossing_lon = a_lon + (lat - a_lat) / (b_lat - a_lat) * (b_lon - a_lon)
            if lon < crossing_lon:
                inside = not inside
    return inside


def point_in_rings(point: Point, rings: list[Ring]) -> bool:
    """Even-odd rule across all rings, so enclaves/holes are handled."""
    return sum(point_in_ring(point, ring) for ring in rings) % 2 == 1


def rings_bbox(rings: list[Ring]) -> tuple[float, float, float, float]:
    """Returns (south, west, north, east)."""
    points = [p for ring in rings for p in ring]
    if not points:
        raise ValueError("no points")
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (min(lats), min(lons), max(lats), max(lons))


def haversine_m(a: Point, b: Point) -> float:
    earth_radius_m = 6_371_000.0
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    d_lat = lat2 - lat1
    d_lon = math.radians(b[1] - a[1])
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(h))


def rings_to_geojson(rings: list[Ring]) -> dict:
    return {
        "type": "MultiPolygon",
        "coordinates": [[[[lon, lat] for lat, lon in ring]] for ring in rings],
    }
