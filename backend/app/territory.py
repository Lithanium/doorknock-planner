from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.blockface import Blockface
from app.geocode import normalise_street
from app.osm.boundary import Point, haversine_m
from app.stops import sort_key
from app.walkgraph import NodeKey, node_key

MAX_TEAMS = 8
"""What the UI offers; nothing in the algorithm depends on it."""

UNIT_ADJACENCY_M = 250.0
"""Centroid distance under which two blockfaces count as neighbours anyway.

On-network blockfaces meet through shared span nodes - two streets that cross
share the intersection node, and both sides of one span share its whole path -
so this only exists for off-network fallbacks, which have no path geometry at
all. 250 m is comfortably above a Kew block's centroid spacing and comfortably
below the kilometres separating the district's duplicate street names.
"""

OVERSIZE_TOLERANCE = 1.2
"""How far past an even share one street may go before it has to be split.

"No street split between teams" is the rule, but it is not always satisfiable:
inside a small radius one long street can hold more work than a whole team's
share, and refusing to split it would just hand that team the imbalance
instead. A street is kept whole until it exceeds the share by this factor,
then cut into contiguous house-number-order chunks - and every street this
happens to is reported, not hidden.
"""

_BALANCE_PASSES = 200
_PLATEAU_EPS = 0.05  # minutes; trades this close to even still count as even


@dataclass
class _Unit:
    """The indivisible piece territories are built from: one street's run.

    Blockfaces of one street that touch (same span, shared intersection node,
    or near-coincident fallback clusters) form one unit, so no street is split
    between teams unless the unit itself had to be cut for being bigger than a
    team's whole share.
    """

    unit_id: str
    street: str
    blockfaces: list[Blockface] = field(default_factory=list)
    nodes: set[NodeKey] = field(default_factory=set)

    @property
    def minutes(self) -> float:
        return sum(b.minutes for b in self.blockfaces)

    @property
    def centroid(self) -> Point:
        lats = [b.centroid[0] for b in self.blockfaces]
        lons = [b.centroid[1] for b in self.blockfaces]
        return (sum(lats) / len(lats), sum(lons) / len(lons))


@dataclass
class Territory:
    """One team's share of the session area: a connected run of whole streets."""

    team: int
    blockfaces: list[Blockface]
    contiguous: bool

    @property
    def minutes(self) -> float:
        return sum(b.minutes for b in self.blockfaces)

    @property
    def door_count(self) -> int:
        return sum(b.door_count for b in self.blockfaces)

    @property
    def stop_count(self) -> int:
        return sum(b.stop_count for b in self.blockfaces)

    @property
    def streets(self) -> list[str]:
        seen: dict[str, None] = {}
        for face in self.blockfaces:
            seen.setdefault(face.street)
        return list(seen)

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "minutes": round(self.minutes, 1),
            "doors": self.door_count,
            "stops": self.stop_count,
            "blockfaces": len(self.blockfaces),
            "streets": self.streets,
            "contiguous": self.contiguous,
        }


@dataclass
class TerritoryPlan:
    territories: list[Territory]
    split_streets: list[str]

    @property
    def spread_pct(self) -> float:
        """Max-to-min workload gap as a share of the mean, the balance figure."""
        minutes = [t.minutes for t in self.territories if t.blockfaces]
        if len(minutes) < 2:
            return 0.0
        mean = sum(minutes) / len(minutes)
        return 0.0 if mean == 0 else (max(minutes) - min(minutes)) / mean

    def team_of(self, blockface_id: str) -> int | None:
        for territory in self.territories:
            for face in territory.blockfaces:
                if face.blockface_id == blockface_id:
                    return territory.team
        return None


def build_territories(
    blockfaces: list[Blockface], hub: Point, teams: int
) -> TerritoryPlan:
    """Partitions blockfaces into balanced, contiguous territories around a hub.

    Streets stay whole (a unit is one street's touching run of blockfaces)
    unless a single street exceeds a team's share, in which case it is cut in
    house-number order and reported in ``split_streets``. Every blockface lands
    in exactly one territory - asserted, not assumed.
    """
    if teams < 1:
        raise ValueError("teams must be at least 1")
    if not blockfaces:
        return TerritoryPlan(
            territories=[Territory(n + 1, [], True) for n in range(teams)],
            split_streets=[],
        )

    total_minutes = sum(b.minutes for b in blockfaces)
    share = total_minutes / teams
    units, split_streets = _build_units(blockfaces, share)
    adjacency = _unit_adjacency(units)

    assignment = _grow_regions(units, adjacency, hub, teams)
    _rebalance(units, adjacency, assignment, teams)

    territories = []
    for team in range(teams):
        members = [u for u, t in assignment.items() if t == team]
        faces = [b for index in members for b in units[index].blockfaces]
        faces.sort(key=lambda b: (b.street, sort_key(b.number_range[0]), b.side))
        territories.append(
            Territory(
                team=team + 1,
                blockfaces=faces,
                contiguous=_component_count(members, adjacency) <= 1,
            )
        )

    assigned = [b.blockface_id for t in territories for b in t.blockfaces]
    assert len(assigned) == len(blockfaces), "a blockface was dropped"
    assert len(set(assigned)) == len(assigned), "a blockface is in two territories"
    return TerritoryPlan(territories=territories, split_streets=sorted(split_streets))


def _face_nodes(face: Blockface) -> set[NodeKey]:
    return {node_key(point) for chain in face.path for point in chain}


def _span_base(face: Blockface) -> str:
    return face.blockface_id.rsplit("#", 1)[0]


def _build_units(
    blockfaces: list[Blockface], share_minutes: float
) -> tuple[list[_Unit], set[str]]:
    """Groups each street's touching blockfaces into one unit, splitting only
    units too big for any team to hold."""
    by_street: dict[str, list[Blockface]] = defaultdict(list)
    for face in blockfaces:
        by_street[normalise_street(face.street)].append(face)

    units: list[_Unit] = []
    split_streets: set[str] = set()
    for street, faces in sorted(by_street.items()):
        for group in _street_groups(faces):
            group.sort(key=lambda b: (sort_key(b.number_range[0]), b.side))
            chunks = _split_oversized(group, share_minutes)
            if len(chunks) > 1:
                split_streets.add(group[0].street)
            for index, chunk in enumerate(chunks):
                unit = _Unit(unit_id=f"{street}/{len(units)}.{index}", street=chunk[0].street)
                for face in chunk:
                    unit.blockfaces.append(face)
                    unit.nodes |= _face_nodes(face)
                units.append(unit)
    return units, split_streets


def _street_groups(faces: list[Blockface]) -> list[list[Blockface]]:
    """Union-find over one street's blockfaces: same span, shared node, or
    near-coincident centroids (the off-network case) join a group.

    Duplicate street names kilometres apart share nothing here, so the two
    Mary Streets always come out as separate units.
    """
    parent = list(range(len(faces)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    by_span: dict[str, int] = {}
    by_node: dict[NodeKey, int] = {}
    for index, face in enumerate(faces):
        span = _span_base(face)
        if span in by_span:
            union(index, by_span[span])
        by_span[span] = index
        for node in _face_nodes(face):
            if node in by_node:
                union(index, by_node[node])
            by_node[node] = index
    for a in range(len(faces)):
        for b in range(a + 1, len(faces)):
            if haversine_m(faces[a].centroid, faces[b].centroid) <= UNIT_ADJACENCY_M:
                union(a, b)

    groups: dict[int, list[Blockface]] = defaultdict(list)
    for index, face in enumerate(faces):
        groups[find(index)].append(face)
    return list(groups.values())


def _split_oversized(
    faces: list[Blockface], share_minutes: float
) -> list[list[Blockface]]:
    """Cuts a unit bigger than a team's share into contiguous chunks.

    Faces arrive in house-number order and stay in it, mirroring how
    oversized blockfaces themselves are split in `blockface.py`.
    """
    total = sum(b.minutes for b in faces)
    if share_minutes <= 0 or total <= share_minutes * OVERSIZE_TOLERANCE:
        return [faces]
    parts = min(len(faces), max(2, round(total / share_minutes)))
    target = total / parts
    chunks: list[list[Blockface]] = []
    current: list[Blockface] = []
    used = 0.0
    for index, face in enumerate(faces):
        current.append(face)
        used += face.minutes
        remaining_faces = len(faces) - index - 1
        remaining_chunks = parts - len(chunks) - 1
        if remaining_chunks <= 0:
            continue
        if remaining_faces <= remaining_chunks or used >= target:
            chunks.append(current)
            current = []
            used = 0.0
    if current:
        chunks.append(current)
    return chunks


_ADJACENCY_CELL_DEG = 0.003  # ~330 m N-S, comfortably above UNIT_ADJACENCY_M


def _unit_adjacency(units: list[_Unit]) -> list[set[int]]:
    """Which units touch: a shared network node, or a pair of nearby blockface
    centroids for the off-network fallbacks that have no geometry.

    Proximity is judged blockface-to-blockface rather than on unit centroids:
    a long street's centroid sits far from both its ends, and units that
    plainly interleave on the ground would otherwise never count as
    neighbours. A coarse grid keeps the comparison near-linear.
    """
    adjacency: list[set[int]] = [set() for _ in units]
    by_node: dict[NodeKey, list[int]] = defaultdict(list)
    for index, unit in enumerate(units):
        for node in unit.nodes:
            by_node[node].append(index)
    for indexes in by_node.values():
        for a in indexes:
            for b in indexes:
                if a != b:
                    adjacency[a].add(b)

    cells: dict[tuple[int, int], list[tuple[int, Point]]] = defaultdict(list)
    for index, unit in enumerate(units):
        for face in unit.blockfaces:
            centroid = face.centroid
            cell = (
                math.floor(centroid[0] / _ADJACENCY_CELL_DEG),
                math.floor(centroid[1] / _ADJACENCY_CELL_DEG),
            )
            cells[cell].append((index, centroid))
    for (row, col), members in cells.items():
        neighbours = [
            entry
            for d_row in (0, 1)
            for d_col in ((0, 1) if d_row == 0 else (-1, 0, 1))
            for entry in cells.get((row + d_row, col + d_col), [])
        ]
        for unit_a, point_a in members:
            for unit_b, point_b in neighbours:
                if unit_a == unit_b or unit_b in adjacency[unit_a]:
                    continue
                if haversine_m(point_a, point_b) <= UNIT_ADJACENCY_M:
                    adjacency[unit_a].add(unit_b)
                    adjacency[unit_b].add(unit_a)
    return adjacency


def _pick_seeds(units: list[_Unit], hub: Point, teams: int) -> list[int]:
    """Seed units near the hub but spread apart, so territories radiate outward
    in different directions rather than nesting inside each other."""
    order = sorted(range(len(units)), key=lambda i: haversine_m(units[i].centroid, hub))
    pool = order[: max(teams * 4, teams)]
    seeds = [pool[0]]
    while len(seeds) < min(teams, len(units)):
        candidates = [i for i in pool if i not in seeds]
        if not candidates:
            candidates = [i for i in order if i not in seeds]
        seeds.append(
            max(
                candidates,
                key=lambda i: min(
                    haversine_m(units[i].centroid, units[s].centroid) for s in seeds
                ),
            )
        )
    return seeds


def _grow_regions(
    units: list[_Unit], adjacency: list[set[int]], hub: Point, teams: int
) -> dict[int, int]:
    """Balanced region growing: the lightest team claims the nearest unassigned
    unit touching its region, so regions stay compact while minutes even out."""
    seeds = _pick_seeds(units, hub, teams)
    assignment: dict[int, int] = {}
    minutes = [0.0] * teams
    for team, seed in enumerate(seeds):
        assignment[seed] = team
        minutes[team] += units[seed].minutes

    unassigned = set(range(len(units))) - set(assignment)
    seed_points = [units[s].centroid for s in seeds] + [hub] * (teams - len(seeds))
    while unassigned:
        claimed = False
        for team in sorted(range(teams), key=lambda t: minutes[t]):
            frontier = {
                n
                for u, t in assignment.items()
                if t == team
                for n in adjacency[u]
                if n in unassigned
            }
            if not frontier:
                continue
            best = min(
                frontier,
                key=lambda i: haversine_m(units[i].centroid, seed_points[team]),
            )
            assignment[best] = team
            minutes[team] += units[best].minutes
            unassigned.discard(best)
            claimed = True
            break
        if not claimed:
            # The scope graph is disconnected (an off-network pocket, say).
            # Hand the whole nearest component to the lightest team so it at
            # least stays in one piece; that team's territory is then honestly
            # reported as non-contiguous.
            team = min(range(teams), key=lambda t: minutes[t])
            start = min(
                unassigned,
                key=lambda i: haversine_m(units[i].centroid, seed_points[team]),
            )
            component = _component_of(start, unassigned, adjacency)
            for index in component:
                assignment[index] = team
                minutes[team] += units[index].minutes
                unassigned.discard(index)
    return assignment


def _rebalance(
    units: list[_Unit],
    adjacency: list[set[int]],
    assignment: dict[int, int],
    teams: int,
) -> None:
    """Boundary trades: shift a unit from a heavier team to a lighter
    neighbouring team whenever that evens the workload out.

    Judged on the variance of team minutes, so a chain of moves can walk work
    across the map even when the heaviest and lightest regions never touch.
    Both regions must stay in one piece for a move to count.
    """
    minutes = [0.0] * teams
    members: list[set[int]] = [set() for _ in range(teams)]
    for index, team in assignment.items():
        minutes[team] += units[index].minutes
        members[team].add(index)

    def spread() -> float:
        return max(minutes) - min(minutes)

    best_spread = spread()
    best_assignment = dict(assignment)
    last_left: dict[int, int] = {}  # unit -> team it most recently left

    for _ in range(_BALANCE_PASSES):
        # (gain, unit, receiver): most negative gain in the sum of squared
        # team minutes first; zero-gain moves from heavier to lighter teams
        # are allowed so equal-weight units can still cascade across the map.
        best: tuple[float, int, int] | None = None
        for index, donor in assignment.items():
            weight = units[index].minutes
            if weight <= 0:
                continue
            receivers = {assignment[n] for n in adjacency[index]} - {donor}
            for receiver in receivers:
                # Negative delta flattens the pair; near-zero swaps which of
                # the two is heavier, which is worth doing only as a stepping
                # stone (the snapshot below keeps the best layout seen).
                gain = weight - (minutes[donor] - minutes[receiver])
                if gain > _PLATEAU_EPS:
                    continue
                if gain > -_PLATEAU_EPS and minutes[donor] <= minutes[receiver]:
                    continue
                if last_left.get(index) == receiver:
                    continue
                if best is not None and gain >= best[0]:
                    continue
                if _component_count(list(members[donor] - {index}), adjacency) > 1:
                    continue
                best = (gain, index, receiver)
        if best is None:
            break
        _gain, index, receiver = best
        donor = assignment[index]
        assignment[index] = receiver
        last_left[index] = donor
        members[donor].discard(index)
        members[receiver].add(index)
        minutes[donor] -= units[index].minutes
        minutes[receiver] += units[index].minutes
        if spread() < best_spread:
            best_spread = spread()
            best_assignment = dict(assignment)

    assignment.clear()
    assignment.update(best_assignment)


def _component_of(start: int, allowed: set[int], adjacency: list[set[int]]) -> set[int]:
    component = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for neighbour in adjacency[current]:
            if neighbour in allowed and neighbour not in component:
                component.add(neighbour)
                queue.append(neighbour)
    return component


def _component_count(members: list[int], adjacency: list[set[int]]) -> int:
    remaining = set(members)
    count = 0
    while remaining:
        count += 1
        component = _component_of(next(iter(remaining)), remaining, adjacency)
        remaining -= component
    return count
