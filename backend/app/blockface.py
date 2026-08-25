from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass, field, replace

from app.geocode import normalise_street, spatial_clusters
from app.osm.boundary import Point, assemble_rings, haversine_m
from app.osm.snapshot import WalkWay
from app.stops import Stop, sort_key
from app.walkgraph import (
    MAX_SNAP_DISTANCE_M,
    WALKING_SPEED_M_PER_MIN,
    NodeKey,
    is_walkable,
    node_key,
    project_to_segment,
)

BUSY_HIGHWAY_CLASSES = frozenset(
    {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"}
)
"""Classes a pair should never criss-cross between houses."""

BUSY_LANE_COUNT = 4
BUSY_MAXSPEED_KMH = 60
"""Kew's residential streets are 50; 60 and above is an arterial."""

MAX_STREET_OFFSET_M = MAX_SNAP_DISTANCE_M
"""Past this a stop is not plausibly on the street it claims, so it falls back.

Measured on the district: 98% of stops sit within 89 m of their own street's
centreline, and the tail flattens out past 300 m - the same bound `walkgraph`
uses to decide a point is not near the network at all. Stops beyond it are on
boundary roads whose carriageway was clipped out of the extract, not merely set
back behind a long drive. There is no risk of a generous radius grabbing a
same-named street elsewhere: the nearest span always wins, and the district's
duplicate street names are kilometres apart.
"""

MAX_BLOCKFACE_MINUTES = 45.0
"""A quarter of a 3-hour session: the largest run worth treating as atomic.

Some Kew streets run a long way without a cross street - Wiltshire Drive is
one 274-minute run, half again longer than a whole session - and a unit that
big cannot be assigned to a team without blowing the budget or being broken up
by the router anyway. Oversized runs are therefore pre-split into contiguous
parts in house-number order, which keeps every promise atomicity exists to
make: one street, one side, no zigzagging, no street left half-done by
accident.
"""

_LEADING_DIGITS_RE = re.compile(r"^(\d+)")

EVEN, ODD, BOTH = "even", "odd", "both"


def parity(number: str) -> str:
    """Australian numbering puts evens on one side of the street and odds on the other.

    Every address in the district extract starts with a digit, so this never
    has to guess; numbers that somehow do not are treated as even rather than
    forming a third phantom side.
    """
    match = _LEADING_DIGITS_RE.match(number.strip())
    return EVEN if match is None or int(match.group(1)) % 2 == 0 else ODD


def _maxspeed_kmh(tags: dict[str, str]) -> int | None:
    match = _LEADING_DIGITS_RE.match(tags.get("maxspeed", "").strip())
    return int(match.group(1)) if match else None


def _lane_count(tags: dict[str, str]) -> int | None:
    match = _LEADING_DIGITS_RE.match(tags.get("lanes", "").strip())
    return int(match.group(1)) if match else None


def busiest_class(classes: Iterable[str]) -> str:
    """The most arterial highway class present, or "" if there are none."""
    return max(classes, default="", key=lambda h: (h in BUSY_HIGHWAY_CLASSES, h))


def is_busy(ways: list[WalkWay]) -> bool:
    """Whether crossing mid-block is unreasonable, from road class and speed.

    Judged on the busiest way in the span: one arterial segment is enough to
    make the whole run a one-side-at-a-time job.
    """
    for way in ways:
        if way.highway in BUSY_HIGHWAY_CLASSES:
            return True
        lanes = _lane_count(way.tags)
        if lanes is not None and lanes >= BUSY_LANE_COUNT:
            return True
        speed = _maxspeed_kmh(way.tags)
        if speed is not None and speed >= BUSY_MAXSPEED_KMH:
            return True
    return False


@dataclass(frozen=True, slots=True)
class Blockface:
    """One atomic unit of doorknocking work: a run of stops a pair walks in one go.

    Atomic is the point. Phase 5's router may order blockfaces however it
    likes but may never split one, which is what stops a plan zigzagging
    across a street or abandoning a street half-done.
    """

    blockface_id: str
    street: str
    side: str
    stops: tuple[Stop, ...]
    one_side_per_pass: bool
    highway: str
    length_m: float
    path: tuple[tuple[Point, ...], ...] = ()
    off_network: bool = False
    clipped: bool = False
    network_nodes: frozenset[NodeKey] = frozenset()
    """The untrimmed span's nodes, for deciding which runs touch on the ground.

    Kept separate from `path` because clipping a run to a session radius
    shortens what a pair walks but changes nothing about which blocks adjoin
    which. Territory contiguity has to be judged on the street network, not on
    this session's extents.
    """

    @property
    def stop_count(self) -> int:
        return len(self.stops)

    def clipped_to_stops(self, keep: Container[str]) -> Blockface | None:
        """This blockface with only `keep`'s stops, or None if none survive.

        A session radius cuts through the middle of blocks, and a blockface
        that merely *touches* the radius must not drag the rest of its street
        along: at a 100 m radius around Kew Junction that pulled in 42
        out-of-radius stops against 17 inside it. The run is trimmed to the
        stops that qualify, and its geometry and walking length are trimmed
        with them so the map and the workload agree.
        """
        kept = tuple(s for s in self.stops if s.stop_id in keep)
        if len(kept) == len(self.stops):
            return self
        if not kept:
            return None
        path = _trim_path(self.path, kept)
        return replace(
            self,
            stops=kept,
            path=path,
            length_m=_path_length(path) if path else _stop_chain_length(kept),
            clipped=True,
        )

    @property
    def door_count(self) -> int:
        return sum(s.door_count for s in self.stops)

    @property
    def gated_candidates(self) -> int:
        return sum(1 for s in self.stops if s.is_gated_candidate)

    @property
    def number_range(self) -> tuple[str, str]:
        return (self.stops[0].number, self.stops[-1].number)

    @property
    def centroid(self) -> Point:
        return (
            sum(s.lat for s in self.stops) / self.stop_count,
            sum(s.lon for s in self.stops) / self.stop_count,
        )

    @property
    def dwell_minutes(self) -> float:
        return sum(s.dwell_seconds for s in self.stops) / 60

    @property
    def walk_minutes(self) -> float:
        """Walking the run itself, ignoring how the pair got here.

        A both-sides blockface still walks the span once - the pair crosses
        back and forth as they go - whereas a one-side-per-pass blockface is
        one full-length traversal per side.
        """
        return self.length_m / WALKING_SPEED_M_PER_MIN

    @property
    def minutes(self) -> float:
        return self.dwell_minutes + self.walk_minutes

    @property
    def label(self) -> str:
        low, high = self.number_range
        if low == high:
            span = f"#{low}"
        else:
            # OSM house numbers can themselves be ranges ("30-38 Cotham Road"),
            # and "#2-#30-38" is unreadable.
            joiner = " to " if "-" in low or "-" in high else "-"
            span = f"#{low}{joiner}#{high}"
        side = "" if self.side == BOTH else f" ({self.side})"
        doors = "door" if self.door_count == 1 else "doors"
        return (
            f"{self.street}{side} {span} - {self.door_count} {doors}"
            f" - {round(self.minutes)} min"
        )

    def to_dict(self) -> dict:
        low, high = self.number_range
        return {
            "id": self.blockface_id,
            "street": self.street,
            "side": self.side,
            "label": self.label,
            "from_number": low,
            "to_number": high,
            "stops": self.stop_count,
            "doors": self.door_count,
            "gated_candidates": self.gated_candidates,
            "one_side_per_pass": self.one_side_per_pass,
            "highway": self.highway,
            "length_m": round(self.length_m, 1),
            "dwell_minutes": round(self.dwell_minutes, 1),
            "walk_minutes": round(self.walk_minutes, 1),
            "minutes": round(self.minutes, 1),
            "off_network": self.off_network,
            "clipped": self.clipped,
        }


@dataclass
class _Span:
    """A run of one street's carriageway between two intersections."""

    span_id: str
    edges: list[tuple[NodeKey, NodeKey]] = field(default_factory=list)
    ways: list[WalkWay] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return sum(haversine_m(a, b) for a, b in self.edges)

    @property
    def highway(self) -> str:
        """The busiest class in the run, since that is what governs crossing."""
        return busiest_class(w.highway for w in self.ways)

    @property
    def path(self) -> tuple[tuple[Point, ...], ...]:
        chains = assemble_rings([[a, b] for a, b in self.edges])
        return tuple(tuple(chain) for chain in chains)


class StreetNetwork:
    """Splits each named street into the runs between its intersections.

    An intersection is a node shared with a *differently named* street. Nodes
    where an unnamed footpath or driveway joins are ignored, or every driveway
    in Kew would start a new blockface.
    """

    def __init__(self, ways: list[WalkWay]) -> None:
        named = [w for w in ways if w.name and is_walkable(w)]
        self._spans_by_street = _build_spans(named, _intersection_nodes(named))

    @property
    def street_count(self) -> int:
        return len(self._spans_by_street)

    @property
    def span_count(self) -> int:
        return sum(len(spans) for spans in self._spans_by_street.values())

    def spans_for_street(self, street: str) -> list[_Span]:
        return self._spans_by_street.get(normalise_street(street), [])

    def street_is_busy(self, street: str) -> bool | None:
        """Busyness from every way of that name, or None if the extract has none.

        A stop on Burke Road that failed to match a span is still on Burke
        Road, so its blockface must inherit Burke Road's arterial status from
        the parts of the carriageway that did survive the clip.
        """
        spans = self.spans_for_street(street)
        if not spans:
            return None
        return is_busy([way for span in spans for way in span.ways])

    def street_highway(self, street: str) -> str:
        return busiest_class(span.highway for span in self.spans_for_street(street))

    def span_for(self, street: str, point: Point) -> _Span | None:
        """The run of `street` a point sits against, or None if it is not near one."""
        best: tuple[float, _Span] | None = None
        for span in self.spans_for_street(street):
            for a, b in span.edges:
                _snapped, offset = project_to_segment(point, a, b)
                if best is None or offset < best[0]:
                    best = (offset, span)
        if best is None or best[0] > MAX_STREET_OFFSET_M:
            return None
        return best[1]


def _intersection_nodes(named_ways: list[WalkWay]) -> set[NodeKey]:
    names_at: dict[NodeKey, set[str]] = defaultdict(set)
    for way in named_ways:
        name = normalise_street(way.name or "")
        for point in way.geometry:
            names_at[node_key(point)].add(name)
    return {node for node, names in names_at.items() if len(names) > 1}


def _build_spans(
    named_ways: list[WalkWay], breaks: set[NodeKey]
) -> dict[str, list[_Span]]:
    """Groups each street's edges into maximal chains between intersections."""
    by_street: dict[str, list[tuple[tuple[NodeKey, NodeKey], WalkWay]]] = defaultdict(list)
    for way in named_ways:
        name = normalise_street(way.name or "")
        for a, b in zip(way.geometry, way.geometry[1:]):
            ka, kb = node_key(a), node_key(b)
            if ka != kb:
                by_street[name].append(((ka, kb), way))

    spans_by_street: dict[str, list[_Span]] = {}
    for name, entries in by_street.items():
        groups = _merge_at_non_break_nodes([edge for edge, _way in entries], breaks)
        spans: dict[int, _Span] = {}
        way_ids: dict[int, set[int]] = defaultdict(set)
        for index, (edge, way) in enumerate(entries):
            root = groups[index]
            span = spans.get(root)
            if span is None:
                span = spans[root] = _Span(span_id="")
            span.edges.append(edge)
            if way.osm_id not in way_ids[root]:
                way_ids[root].add(way.osm_id)
                span.ways.append(way)
        # Ordered by lowest edge rather than lowest node: two spans of one
        # street share the intersection node between them but never an edge,
        # so this is a total order and does not depend on way ordering.
        ordered = sorted(spans.values(), key=_span_order)
        used: Counter[str] = Counter()
        for span in ordered:
            # Keyed on the span's own lowest coordinate, so the id survives a
            # re-fetch that reorders ways and is greppable on a map. A V-shaped
            # street whose junction sits at the bottom of the V gives both its
            # spans the same lowest coordinate, hence the suffix; without it
            # two distinct blocks would silently merge into one blockface.
            anchor = min(node for edge in span.edges for node in edge)
            base = f"{name}@{anchor[0]:.5f},{anchor[1]:.5f}"
            used[base] += 1
            span.span_id = base if used[base] == 1 else f"{base}+{used[base]}"
        spans_by_street[name] = ordered
    return spans_by_street


def _span_order(span: _Span) -> tuple[NodeKey, NodeKey]:
    return min(tuple(sorted(edge)) for edge in span.edges)  # type: ignore[return-value]


def _merge_at_non_break_nodes(
    edges: list[tuple[NodeKey, NodeKey]], breaks: set[NodeKey]
) -> list[int]:
    """Union-find over edges, joining any two that meet away from an intersection."""
    parent = list(range(len(edges)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    at_node: dict[NodeKey, list[int]] = defaultdict(list)
    for index, (a, b) in enumerate(edges):
        at_node[a].append(index)
        at_node[b].append(index)
    for node, indexes in at_node.items():
        if node in breaks:
            continue
        first = indexes[0]
        for other in indexes[1:]:
            root_a, root_b = find(first), find(other)
            if root_a != root_b:
                parent[root_a] = root_b
    return [find(index) for index in range(len(edges))]


def build_blockfaces(stops: list[Stop], ways: list[WalkWay]) -> list[Blockface]:
    """Groups stops into atomic blockfaces: one street, one side, one block."""
    network = StreetNetwork(ways)
    on_network: dict[str, tuple[_Span, list[Stop]]] = {}
    off_network: dict[str, list[Stop]] = defaultdict(list)

    for stop in stops:
        span = network.span_for(stop.street, stop.point)
        if span is None:
            off_network[stop.street].append(stop)
            continue
        bucket = on_network.setdefault(span.span_id, (span, []))
        bucket[1].append(stop)

    blockfaces = [
        face
        for span, members in on_network.values()
        for face in _split_by_side(
            members,
            span_id=span.span_id,
            highway=span.highway,
            busy=is_busy(span.ways),
            length_m=span.length_m,
            path=span.path,
            off_network=False,
        )
    ]
    blockfaces.extend(_fallback_blockfaces(off_network, network))
    blockfaces.sort(key=lambda b: (b.street, sort_key(b.number_range[0]), b.side))
    return blockfaces


def _fallback_blockfaces(
    off_network: dict[str, list[Stop]], network: StreetNetwork
) -> list[Blockface]:
    """Blockfaces for stops with no matching street geometry in the extract.

    Mostly boundary roads - Barkers Road, Burke Road, Canterbury Road - whose
    carriageway is clipped away even though their addresses are inside the
    district. Those doors still have to be knocked, so they group by spatial
    cluster instead, which is also what keeps two same-named streets apart
    here.

    A street with no geometry at all is assumed busy. Canterbury Road is one
    of them, and the cost of being wrong in that direction is a slightly
    inefficient route, against sending a pair back and forth across an
    arterial in the other.
    """
    faces = []
    for street, members in off_network.items():
        busy = network.street_is_busy(street)
        clusters = spatial_clusters([door for stop in members for door in stop.doors])
        cluster_of = {
            door.osm_id: index for index, c in enumerate(clusters) for door in c
        }
        grouped: dict[int, list[Stop]] = defaultdict(list)
        for stop in members:
            grouped[cluster_of[stop.doors[0].osm_id]].append(stop)
        for index, group in sorted(grouped.items()):
            faces.extend(
                _split_by_side(
                    group,
                    span_id=f"{normalise_street(street)}~{index}",
                    highway=network.street_highway(street),
                    busy=True if busy is None else busy,
                    length_m=_chain_length(group),
                    path=(),
                    off_network=True,
                )
            )
    return faces


def _chain_length(stops: list[Stop]) -> float:
    """Fallback run length: walking the stops in house-number order."""
    return _stop_chain_length(stops)


def _stop_chain_length(stops: Sequence[Stop]) -> float:
    ordered = sorted(stops, key=lambda s: sort_key(s.number))
    return sum(haversine_m(a.point, b.point) for a, b in zip(ordered, ordered[1:]))


STOP_FRONTAGE_M = 10.0
"""Half a house frontage, so a trimmed run is not a zero-length point."""


def _path_length(path: tuple[tuple[Point, ...], ...]) -> float:
    return sum(
        haversine_m(a, b) for chain in path for a, b in zip(chain, chain[1:])
    )


def _trim_path(
    path: tuple[tuple[Point, ...], ...], stops: Sequence[Stop]
) -> tuple[tuple[Point, ...], ...]:
    """Cuts each chain back to the stretch its own stops actually occupy.

    Every stop is attributed to the single chain it lies closest to, so a span
    made of several disjoint chains only keeps the ones still being knocked.
    """
    if not path:
        return ()
    assigned: dict[int, list[float]] = defaultdict(list)
    for stop in stops:
        best: tuple[float, int, float] | None = None
        for index, chain in enumerate(path):
            offset, along = _project_onto_chain(chain, stop.point)
            if best is None or offset < best[0]:
                best = (offset, index, along)
        if best is not None:
            assigned[best[1]].append(best[2])
    trimmed = []
    for index, chain in enumerate(path):
        alongs = assigned.get(index)
        if not alongs:
            continue
        piece = _slice_chain(
            chain, min(alongs) - STOP_FRONTAGE_M, max(alongs) + STOP_FRONTAGE_M
        )
        if len(piece) >= 2:
            trimmed.append(piece)
    return tuple(trimmed)


def _project_onto_chain(chain: tuple[Point, ...], point: Point) -> tuple[float, float]:
    """(distance to the chain, distance along it) for the nearest point on it."""
    best = (math.inf, 0.0)
    travelled = 0.0
    for a, b in zip(chain, chain[1:]):
        snapped, offset = project_to_segment(point, a, b)
        if offset < best[0]:
            best = (offset, travelled + haversine_m(a, snapped))
        travelled += haversine_m(a, b)
    return best


def _slice_chain(chain: tuple[Point, ...], start_m: float, end_m: float) -> tuple[Point, ...]:
    """The sub-polyline between two distances along a chain, ends interpolated."""
    total = _path_length((chain,))
    start_m, end_m = max(0.0, start_m), min(total, end_m)
    if end_m <= start_m:
        return ()
    points: list[Point] = []
    travelled = 0.0
    for a, b in zip(chain, chain[1:]):
        segment = haversine_m(a, b)
        if segment == 0:
            continue
        finish = travelled + segment
        if finish >= start_m and travelled <= end_m:
            head = _interpolate(a, b, (max(start_m, travelled) - travelled) / segment)
            tail = _interpolate(a, b, (min(end_m, finish) - travelled) / segment)
            if not points:
                points.append(head)
            if tail != points[-1]:
                points.append(tail)
        travelled = finish
    return tuple(points)


def _interpolate(a: Point, b: Point, fraction: float) -> Point:
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def _split_by_side(
    stops: list[Stop],
    *,
    span_id: str,
    highway: str,
    busy: bool,
    length_m: float,
    path: tuple[tuple[Point, ...], ...],
    off_network: bool,
    max_minutes: float = MAX_BLOCKFACE_MINUTES,
) -> list[Blockface]:
    sides: dict[str, list[Stop]] = defaultdict(list)
    for stop in stops:
        sides[parity(stop.number) if busy else BOTH].append(stop)

    nodes = frozenset(node_key(point) for chain in path for point in chain)
    faces = []
    for side, members in sorted(sides.items()):
        ordered = sorted(members, key=lambda s: sort_key(s.number))
        parts = split_into_parts(ordered, length_m, max_minutes)
        for index, (chunk, part_length_m) in enumerate(parts):
            suffix = f"/{index + 1}" if len(parts) > 1 else ""
            faces.append(
                Blockface(
                    blockface_id=f"{span_id}#{side}{suffix}",
                    street=chunk[0].street,
                    side=side,
                    stops=tuple(chunk),
                    one_side_per_pass=busy,
                    highway=highway,
                    length_m=part_length_m,
                    path=path,
                    off_network=off_network,
                    network_nodes=nodes,
                )
            )
    return faces


def split_into_parts(
    stops: list[Stop], length_m: float, max_minutes: float
) -> list[tuple[list[Stop], float]]:
    """Cuts a too-long run into contiguous, roughly equal parts.

    Stops arrive in house-number order and stay in it, so every part is still
    an unbroken stretch of one side of one street. The run's walking distance
    is shared evenly between the parts.
    """
    dwell_minutes = sum(s.dwell_seconds for s in stops) / 60
    total = dwell_minutes + length_m / WALKING_SPEED_M_PER_MIN
    parts = min(len(stops), max(1, math.ceil(total / max_minutes)))
    if parts <= 1:
        return [(stops, length_m)]

    share_m = length_m / parts
    target = total / parts
    walk_share = share_m / WALKING_SPEED_M_PER_MIN
    chunks: list[list[Stop]] = []
    current: list[Stop] = []
    used = walk_share
    for index, stop in enumerate(stops):
        current.append(stop)
        used += stop.dwell_seconds / 60
        remaining_stops = len(stops) - index - 1
        remaining_chunks = parts - len(chunks) - 1
        if remaining_chunks <= 0:
            continue
        # Close the chunk once it is full, but never so eagerly that a later
        # chunk would be left with no stops at all.
        if remaining_stops <= remaining_chunks or used >= target:
            chunks.append(current)
            current = []
            used = walk_share
    if current:
        chunks.append(current)
    return [(chunk, share_m) for chunk in chunks]
