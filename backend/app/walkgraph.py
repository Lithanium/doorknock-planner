from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass

import networkx as nx

from app.osm.boundary import Point, haversine_m
from app.osm.snapshot import Address, WalkWay

WALKING_SPEED_M_PER_MIN = 80.0
"""4.8 km/h - a comfortable pace for a pair carrying pamphlets."""

MAX_SNAP_DISTANCE_M = 300.0
"""Beyond this a point is not usefully 'near' the walking network at all."""

_GRID_CELL_DEG = 0.0005  # ~55 m N-S, ~44 m E-W at Melbourne latitudes
_NODE_PRECISION = 7

NodeKey = tuple[float, float]


def node_key(point: Point) -> NodeKey:
    return (round(point[0], _NODE_PRECISION), round(point[1], _NODE_PRECISION))


def _edge_key(a: NodeKey, b: NodeKey) -> tuple[NodeKey, NodeKey]:
    return (a, b) if a <= b else (b, a)


def is_walkable(way: WalkWay) -> bool:
    """Pedestrian filter over the already highway-filtered snapshot ways.

    ``foot`` is the definitive tag: an explicit yes/designated/permissive
    overrides a private access tag (gated estates whose footpaths are open),
    while ``foot=no`` excludes a way whatever its class.
    """
    foot = way.tags.get("foot")
    if foot == "no":
        return False
    if foot in ("yes", "designated", "permissive"):
        return True
    return way.tags.get("access") not in ("private", "no")


@dataclass(frozen=True, slots=True)
class EdgeSnap:
    """A point projected onto its nearest walkable edge."""

    a: NodeKey
    b: NodeKey
    point: Point
    offset_m: float
    to_a_m: float
    to_b_m: float

    @property
    def edge(self) -> tuple[NodeKey, NodeKey]:
        return _edge_key(self.a, self.b)


@dataclass(frozen=True, slots=True)
class WalkRoute:
    points: list[Point]
    distance_m: float

    @property
    def minutes(self) -> float:
        return self.distance_m / WALKING_SPEED_M_PER_MIN


_M_PER_DEG = 111_320.0


def project_to_segment(p: Point, a: Point, b: Point) -> tuple[Point, float]:
    """Nearest point on segment a-b to p, in a local equirectangular frame.

    Returns (snap point, distance in metres). The flat-earth approximation is
    accurate to well under a metre at the sub-300 m distances involved.
    """
    cos_lat = math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * cos_lat, a[0] - p[0]
    bx, by = (b[1] - p[1]) * cos_lat, b[0] - p[0]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    x, y = ax + t * dx, ay + t * dy
    lat = a[0] + t * (b[0] - a[0])
    lon = a[1] + t * (b[1] - a[1])
    return (lat, lon), math.hypot(x, y) * _M_PER_DEG


class WalkGraph:
    """The district's walking network with edge snapping and routing.

    Nodes are coordinates rounded to 1e-7 degrees, so ways that share OSM
    nodes connect and ways that merely cross in the air (a footbridge over
    the Eastern Freeway) do not.
    """

    def __init__(self, ways: list[WalkWay]) -> None:
        self.graph = nx.Graph()
        # The graph is shared across requests and route()/distances_from()
        # temporarily inject sentinel nodes into it, so the mutating regions
        # must not overlap (the sync API endpoints run in a thread pool).
        self._lock = threading.Lock()
        self._grid: dict[tuple[int, int], list[tuple[NodeKey, NodeKey]]] = defaultdict(list)
        seen: set[tuple[NodeKey, NodeKey]] = set()
        for way in ways:
            if not is_walkable(way):
                continue
            for a, b in zip(way.geometry, way.geometry[1:]):
                ka, kb = node_key(a), node_key(b)
                if ka == kb:
                    continue
                weight = haversine_m(ka, kb)
                if not self.graph.has_edge(ka, kb) or weight < self.graph[ka][kb]["weight"]:
                    self.graph.add_edge(ka, kb, weight=weight)
                edge = _edge_key(ka, kb)
                if edge not in seen:
                    seen.add(edge)
                    self._index_segment(edge)

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / _GRID_CELL_DEG)), int(math.floor(lon / _GRID_CELL_DEG)))

    def _index_segment(self, edge: tuple[NodeKey, NodeKey]) -> None:
        (a_lat, a_lon), (b_lat, b_lon) = edge
        row_min, col_min = self._cell(min(a_lat, b_lat), min(a_lon, b_lon))
        row_max, col_max = self._cell(max(a_lat, b_lat), max(a_lon, b_lon))
        for row in range(row_min, row_max + 1):
            for col in range(col_min, col_max + 1):
                self._grid[(row, col)].append(edge)

    def snap(self, lat: float, lon: float, max_distance_m: float = MAX_SNAP_DISTANCE_M) -> EdgeSnap | None:
        """Nearest point on the network, searching the grid ring by ring."""
        centre = self._cell(lat, lon)
        cell_min_m = _GRID_CELL_DEG * _M_PER_DEG * math.cos(math.radians(lat))
        max_ring = int(max_distance_m / cell_min_m) + 2
        best: tuple[float, Point, NodeKey, NodeKey] | None = None
        seen: set[tuple[NodeKey, NodeKey]] = set()
        for ring in range(max_ring + 1):
            for row in range(centre[0] - ring, centre[0] + ring + 1):
                for col in range(centre[1] - ring, centre[1] + ring + 1):
                    if max(abs(row - centre[0]), abs(col - centre[1])) != ring:
                        continue
                    for a, b in self._grid.get((row, col), ()):
                        if (a, b) in seen:
                            continue
                        seen.add((a, b))
                        point, offset = project_to_segment((lat, lon), a, b)
                        if best is None or offset < best[0]:
                            best = (offset, point, a, b)
            # Every unexplored segment lies beyond ring `ring`, i.e. at least
            # `ring` whole cells away, so a hit at or under that distance
            # cannot be beaten and the search can stop.
            if best is not None and best[0] <= ring * cell_min_m:
                break
        if best is None or best[0] > max_distance_m:
            return None
        offset, point, a, b = best
        return EdgeSnap(
            a=a,
            b=b,
            point=point,
            offset_m=offset,
            to_a_m=haversine_m(point, a),
            to_b_m=haversine_m(point, b),
        )

    def snap_addresses(self, addresses: list[Address]) -> dict[str, EdgeSnap]:
        snaps: dict[str, EdgeSnap] = {}
        for address in addresses:
            snap = self.snap(address.lat, address.lon)
            if snap is not None:
                snaps[address.osm_id] = snap
        return snaps

    def route(self, origin: Point, destination: Point) -> WalkRoute | None:
        """Shortest walking path door to door, or None when unreachable."""
        start = self.snap(*origin)
        end = self.snap(*destination)
        if start is None or end is None:
            return None
        src: NodeKey = (math.nan, 0.0)  # never collides with a real coordinate
        dst: NodeKey = (math.nan, 1.0)
        graph = self.graph
        with self._lock:
            try:
                graph.add_edge(src, start.a, weight=start.to_a_m)
                graph.add_edge(src, start.b, weight=start.to_b_m)
                graph.add_edge(dst, end.a, weight=end.to_a_m)
                graph.add_edge(dst, end.b, weight=end.to_b_m)
                if start.edge == end.edge:
                    along = abs(start.to_a_m - end.to_a_m)
                    graph.add_edge(src, dst, weight=along)
                try:
                    network_m, path = nx.bidirectional_dijkstra(graph, src, dst, weight="weight")
                except nx.NetworkXNoPath:
                    return None
            finally:
                graph.remove_node(src)
                graph.remove_node(dst)
        via = [node for node in path if node not in (src, dst)]
        points: list[Point] = [origin, start.point, *via, end.point, destination]
        return WalkRoute(points=points, distance_m=start.offset_m + network_m + end.offset_m)

    def distances_from(
        self, origin: Point, snaps: dict[str, EdgeSnap], cutoff_m: float
    ) -> dict[str, float]:
        """Walking metres from origin to every snapped address within cutoff.

        One bounded single-source Dijkstra, then each address is reached via
        its snap edge's endpoints (plus the door-to-footpath offsets on both
        ends), which is exact because the snap point lies on that edge.
        """
        start = self.snap(*origin)
        if start is None:
            return {}
        src: NodeKey = (math.nan, 0.0)
        graph = self.graph
        with self._lock:
            try:
                graph.add_edge(src, start.a, weight=start.to_a_m)
                graph.add_edge(src, start.b, weight=start.to_b_m)
                dist = nx.single_source_dijkstra_path_length(
                    graph, src, cutoff=cutoff_m, weight="weight"
                )
            finally:
                graph.remove_node(src)
        result: dict[str, float] = {}
        for osm_id, snap in snaps.items():
            best = math.inf
            for node, along in ((snap.a, snap.to_a_m), (snap.b, snap.to_b_m)):
                if node in dist:
                    best = min(best, dist[node] + along)
            if snap.edge == start.edge:
                best = min(best, abs(snap.to_a_m - start.to_a_m))
            total = start.offset_m + best + snap.offset_m
            if total <= cutoff_m:
                result[osm_id] = total
        return result
