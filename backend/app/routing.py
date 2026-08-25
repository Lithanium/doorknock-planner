from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.blockface import BUSY_HIGHWAY_CLASSES, Blockface
from app.osm.boundary import Point, haversine_m
from app.stops import APPROACH_SECONDS, PER_DOOR_SECONDS, dwell_seconds
from app.walkgraph import WALKING_SPEED_M_PER_MIN

DETOUR_FACTOR = 1.35
"""Street-grid detour over crow-flies distance, measured on the district.

Real walking routes checked against the walk graph come out at 1.2-1.5x the
straight line (967 m walked for 714 m crow-flies at Kew Junction). Routing
between blockfaces uses centroid distance times this factor rather than a
full graph Dijkstra per pair: the matrix for a session's ~250 blockfaces stays
instant to build, and the error is uniform enough not to change orderings.
"""

RELOAD_SECONDS = 300.0
"""Walking into the hub, refilling both bags, and getting back out: 5 minutes."""

MAX_RELOADS = 8
"""Reload slots offered to the solver; unused ones are dropped free of charge.

Eight restocks in a 3-hour session means restocking every 22 minutes, which no
sane pamphlet count produces; the bound only caps the model size.
"""

_DROP_PENALTY_BASE = 10_000_000
"""Dropping any blockface must cost far more than any detour saves, so houses
are only dropped when the session budget or pamphlet supply truly runs out."""

_SOLUTION_LIMIT = 2_000
"""Search is bounded by solution count, not wall time, so the same input
always yields the same route - the property the golden-file test pins."""

_TURN_ANGLE_DEG = 60.0
_TURN_PENALTY_M = 25.0
_STREET_CHANGE_PENALTY_M = 60.0
_BUSY_CROSSING_PENALTY_M = 150.0
_REWALK_PENALTY_M = 100.0
"""Smoothing weights, in equivalent walking metres.

A route a human will actually follow trades a little distance for fewer
decisions: carry on down the same street rather than turning off it (a street
change costs ~45 s of "which way now?"), avoid doubling back (a >60-degree
turn), never cross an arterial mid-plan when it can be avoided, and do not
send the pair back along a street they have already worked.
"""


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Everything the field organiser can turn: all of Phase 5's dials.

    ``take_up`` is the share of doors that take a pamphlet - 1.0 means one is
    left at every door (letterboxing), lower values model conversation-first
    sessions where many doors decline. It scales pamphlet consumption only;
    the pair still knocks every door, so dwell time is unaffected.
    """

    pamphlets: int = 200
    take_up: float = 1.0
    approach_seconds: float = APPROACH_SECONDS
    per_door_seconds: float = PER_DOOR_SECONDS
    speed_m_per_min: float = WALKING_SPEED_M_PER_MIN
    session_minutes: float = 180.0
    capacity_enabled: bool = True

    def __post_init__(self) -> None:
        if self.pamphlets < 1:
            raise ValueError("pamphlets must be at least 1")
        if not 0.0 < self.take_up <= 1.0:
            raise ValueError("take_up must be in (0, 1]")
        if self.speed_m_per_min <= 0:
            raise ValueError("speed_m_per_min must be positive")
        if self.session_minutes <= 0:
            raise ValueError("session_minutes must be positive")

    def demand(self, face: Blockface) -> int:
        """Pamphlets a blockface consumes, rounded up so supply never lies."""
        return max(1, math.ceil(face.door_count * self.take_up))

    def service_seconds(self, face: Blockface) -> float:
        """Knocking the run: per-stop dwell at this config's rates, plus
        walking the run itself."""
        dwell = sum(
            dwell_seconds(
                s.door_count,
                approach=self.approach_seconds,
                per_door=self.per_door_seconds,
            )
            for s in face.stops
        )
        return dwell + (face.length_m / self.speed_m_per_min) * 60.0


@dataclass(frozen=True, slots=True)
class Visit:
    """One entry in the pair's ordered plan."""

    kind: str  # "hub" | "reload" | "blockface"
    blockface: Blockface | None
    arrive_minute: float
    pamphlets_left: int


@dataclass
class RoutePlan:
    visits: list[Visit]
    dropped: list[Blockface]
    config: RouteConfig
    hub: Point
    travel_m: float = field(default=0.0)

    @property
    def served(self) -> list[Blockface]:
        return [v.blockface for v in self.visits if v.blockface is not None]

    @property
    def restock_trips(self) -> int:
        return sum(1 for v in self.visits if v.kind == "reload")

    @property
    def knock_seconds(self) -> float:
        return sum(
            dwell_seconds(
                s.door_count,
                approach=self.config.approach_seconds,
                per_door=self.config.per_door_seconds,
            )
            for face in self.served
            for s in face.stops
        )

    @property
    def walk_m(self) -> float:
        """Every metre on foot: between blockfaces and along them."""
        return self.travel_m + sum(f.length_m for f in self.served)

    @property
    def walk_seconds(self) -> float:
        return (self.walk_m / self.config.speed_m_per_min) * 60.0

    @property
    def total_minutes(self) -> float:
        return (
            self.walk_seconds
            + self.knock_seconds
            + self.restock_trips * RELOAD_SECONDS
        ) / 60.0

    @property
    def doors_served(self) -> int:
        return sum(f.door_count for f in self.served)

    @property
    def doors_dropped(self) -> int:
        return sum(f.door_count for f in self.dropped)

    @property
    def coverage_pct(self) -> float:
        total = self.doors_served + self.doors_dropped
        return 100.0 * self.doors_served / total if total else 0.0

    def metrics(self) -> dict:
        moving = self.walk_seconds + self.knock_seconds
        return {
            "walk_km": round(self.walk_m / 1000, 2),
            "walk_minutes": round(self.walk_seconds / 60, 1),
            "knock_minutes": round(self.knock_seconds / 60, 1),
            "walking_pct": round(100 * self.walk_seconds / moving, 1) if moving else 0.0,
            "knocking_pct": round(100 * self.knock_seconds / moving, 1) if moving else 0.0,
            "restock_trips": self.restock_trips,
            "restock_minutes": round(self.restock_trips * RELOAD_SECONDS / 60, 1),
            "total_minutes": round(self.total_minutes, 1),
            "session_minutes": self.config.session_minutes,
            "doors_served": self.doors_served,
            "doors_dropped": self.doors_dropped,
            "blockfaces_served": len(self.served),
            "blockfaces_dropped": len(self.dropped),
            "coverage_pct": round(self.coverage_pct, 1),
            "pamphlets_per_load": self.config.pamphlets,
            "capacity_enabled": self.config.capacity_enabled,
        }


def plan_route(
    blockfaces: list[Blockface], hub: Point, config: RouteConfig = RouteConfig()
) -> RoutePlan:
    """One pair's session: hub out, knock whole blockfaces, restock, hub home.

    Blockfaces are atomic - the solver orders them but never splits one. The
    pamphlet supply is a routing dimension that reload visits at the hub reset,
    the session length is a hard time budget, and every blockface carries a
    drop penalty that falls with distance from the hub, so when something has
    to give it is always the farthest houses that go.
    """
    faces = sorted(blockfaces, key=lambda b: b.blockface_id)
    if not faces:
        return RoutePlan(visits=[], dropped=[], config=config, hub=hub)

    # Node 0 is the hub; nodes 1..len(faces) are blockfaces; the rest are
    # reload copies of the hub that hand back a full load of pamphlets.
    reloads = _reload_count(faces, config)
    positions: list[Point] = [hub] + [f.centroid for f in faces] + [hub] * reloads
    face_at = {i + 1: f for i, f in enumerate(faces)}
    first_reload = 1 + len(faces)
    size = len(positions)

    travel_s = [[0] * size for _ in range(size)]
    for a in range(size):
        for b in range(size):
            if a != b:
                metres = haversine_m(positions[a], positions[b]) * DETOUR_FACTOR
                travel_s[a][b] = round((metres / config.speed_m_per_min) * 60.0)

    service_s = [0] * size
    for node, face in face_at.items():
        service_s[node] = round(config.service_seconds(face))
    for node in range(first_reload, size):
        service_s[node] = round(RELOAD_SECONDS)

    manager = pywrapcp.RoutingIndexManager(size, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit(from_index: int, to_index: int) -> int:
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return travel_s[a][b] + service_s[a]

    transit_cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    budget_s = round(config.session_minutes * 60)
    routing.AddDimension(transit_cb, 0, budget_s, True, "time")

    if config.capacity_enabled:
        # The dimension tracks pamphlets *left in the bags*: the pair starts
        # full, each blockface subtracts its demand, and the cumul may never
        # go negative. Reload nodes are the only places with slack, so they
        # are the only places the count can jump back up - and only ever up
        # to a full load.
        def demand(from_index: int) -> int:
            node = manager.IndexToNode(from_index)
            return -config.demand(face_at[node]) if node in face_at else 0

        demand_cb = routing.RegisterUnaryTransitCallback(demand)
        routing.AddDimension(
            demand_cb, config.pamphlets, config.pamphlets, False, "pamphlets"
        )
        pamphlets = routing.GetDimensionOrDie("pamphlets")
        pamphlets.CumulVar(routing.Start(0)).SetValue(config.pamphlets)
        pamphlets.SlackVar(routing.Start(0)).SetValue(0)
        for node in range(1, size):
            index = manager.NodeToIndex(node)
            if node < first_reload:
                pamphlets.SlackVar(index).SetValue(0)

    for node, face in face_at.items():
        # Farther houses are cheaper to drop, so a plan that cannot fit
        # everything always sheds from the outside in.
        distance = haversine_m(face.centroid, hub)
        penalty = _DROP_PENALTY_BASE - round(distance) * 1_000
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)
    for node in range(first_reload, size):
        # Unused reload slots cost nothing to skip.
        routing.AddDisjunction([manager.NodeToIndex(node)], 0)

    params = pywrapcp.DefaultRoutingSearchParameters()
    # PARALLEL_CHEAPEST_INSERTION matters here: arc-chaining strategies build
    # routes that never pick up a reload node, and greedy descent cannot
    # repair that because inserting a dropped blockface needs its reload
    # inserted in the same move. Insertion-based construction places both.
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT
    )
    params.solution_limit = _SOLUTION_LIMIT
    solution = routing.SolveWithParameters(params)
    if solution is None:
        # Nothing fits (a budget shorter than the first blockface, say):
        # an honest empty plan, with everything reported as dropped.
        return RoutePlan(visits=[], dropped=faces, config=config, hub=hub)

    order: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node in face_at or node >= first_reload:
            order.append(node)
        index = solution.Value(routing.NextVar(index))

    order = _smooth(order, face_at, first_reload, positions, hub, config, budget_s, travel_s, service_s)

    served_nodes = {n for n in order if n in face_at}
    dropped = [f for n, f in face_at.items() if n not in served_nodes]
    dropped.sort(key=lambda f: haversine_m(f.centroid, hub), reverse=True)

    visits, travel_m = _build_visits(order, face_at, first_reload, positions, config, travel_s, service_s)
    return RoutePlan(visits=visits, dropped=dropped, config=config, hub=hub, travel_m=travel_m)


def _reload_count(faces: list[Blockface], config: RouteConfig) -> int:
    if not config.capacity_enabled:
        return 0
    total = sum(config.demand(f) for f in faces)
    return min(MAX_RELOADS, max(0, math.ceil(total / config.pamphlets) - 1) + 1)


def _build_visits(
    order: list[int],
    face_at: dict[int, Blockface],
    first_reload: int,
    positions: list[Point],
    config: RouteConfig,
    travel_s: list[list[int]],
    service_s: list[int],
) -> tuple[list[Visit], float]:
    visits: list[Visit] = [Visit("hub", None, 0.0, config.pamphlets)]
    clock_s = 0.0
    left = config.pamphlets
    travel_m = 0.0
    previous = 0
    for node in order:
        clock_s += travel_s[previous][node]
        travel_m += haversine_m(positions[previous], positions[node]) * DETOUR_FACTOR
        if node in face_at:
            face = face_at[node]
            left -= config.demand(face)
            visits.append(Visit("blockface", face, clock_s / 60.0, left))
        else:
            left = config.pamphlets
            visits.append(Visit("reload", None, clock_s / 60.0, left))
        clock_s += service_s[node]
        previous = node
    clock_s += travel_s[previous][0]
    travel_m += haversine_m(positions[previous], positions[0]) * DETOUR_FACTOR
    visits.append(Visit("hub", None, clock_s / 60.0, left))
    return visits, travel_m


def _smooth(
    order: list[int],
    face_at: dict[int, Blockface],
    first_reload: int,
    positions: list[Point],
    hub: Point,
    config: RouteConfig,
    budget_s: int,
    travel_s: list[list[int]],
    service_s: list[int],
) -> list[int]:
    """Deterministic 2-opt over each between-reloads leg, scored for humans.

    The solver optimises seconds; a pair of volunteers optimises not getting
    confused. This pass reverses sub-sequences whenever that lowers a score
    that charges walking metres plus penalties for sharp turns, changing
    street, crossing an arterial and re-walking a street already worked -
    and a move is only kept if the whole plan still fits the session budget.
    """
    busy_segments = _busy_segments(face_at.values())

    def total_seconds(sequence: list[int]) -> int:
        seconds = 0
        previous = 0
        for node in sequence:
            seconds += travel_s[previous][node] + service_s[node]
            previous = node
        return seconds + travel_s[previous][0]

    def improved(sequence: list[int]) -> list[int]:
        # Reload positions are pinned: reversing across one would change
        # where the pamphlet count resets. Each segment smooths on its own.
        segments: list[list[int]] = [[]]
        for node in sequence:
            if node >= first_reload:
                segments.append([node])
                segments.append([])
            else:
                segments[-1].append(node)
        changed = True
        while changed:
            changed = False
            for segment in segments:
                if len(segment) < 3 or segment[0] >= first_reload:
                    continue
                score = _human_score(_flat(segments), face_at, first_reload, positions, busy_segments)
                for i in range(len(segment) - 1):
                    for j in range(i + 2, len(segment) + 1):
                        candidate = segment[:i] + segment[i:j][::-1] + segment[j:]
                        trial = [candidate if s is segment else s for s in segments]
                        flat = _flat(trial)
                        if total_seconds(flat) > budget_s:
                            continue
                        trial_score = _human_score(flat, face_at, first_reload, positions, busy_segments)
                        if trial_score < score - 1e-9:
                            segment[:] = candidate
                            score = trial_score
                            changed = True
        return _flat(segments)

    return improved(order)


def _flat(segments: list[list[int]]) -> list[int]:
    return [node for segment in segments for node in segment]


def _human_score(
    order: list[int],
    face_at: dict[int, Blockface],
    first_reload: int,
    positions: list[Point],
    busy_segments: list[tuple[Point, Point]],
) -> float:
    """Walking metres plus the smoothing penalties, over the whole plan."""
    path = [0, *order, 0]
    score = 0.0
    streets_walked: list[str] = []
    for a, b in zip(path, path[1:]):
        leg = haversine_m(positions[a], positions[b]) * DETOUR_FACTOR
        score += leg
        for seg_a, seg_b in busy_segments:
            if _segments_cross(positions[a], positions[b], seg_a, seg_b):
                score += _BUSY_CROSSING_PENALTY_M
                break
    for previous, current in zip(path, path[1:]):
        prev_street = face_at[previous].street if previous in face_at else None
        cur_street = face_at[current].street if current in face_at else None
        if prev_street and cur_street and prev_street != cur_street:
            score += _STREET_CHANGE_PENALTY_M
        if cur_street:
            if not streets_walked or streets_walked[-1] != cur_street:
                if cur_street in streets_walked:
                    # Back on a street already worked and left: a re-walk.
                    score += _REWALK_PENALTY_M
                streets_walked.append(cur_street)
    for a, b, c in zip(path, path[1:], path[2:]):
        if _turn_angle_deg(positions[a], positions[b], positions[c]) > _TURN_ANGLE_DEG:
            score += _TURN_PENALTY_M
    # Out-and-back tours cost the same walked in either direction. Break the
    # tie toward serving the near-hub houses first: if the session is cut
    # short in the field, what is left undone is the far end.
    faces_in_order = [n for n in order if n in face_at]
    count = len(faces_in_order)
    score += 1e-6 * sum(
        (count - rank) * haversine_m(positions[node], positions[0])
        for rank, node in enumerate(faces_in_order)
    )
    return score


def _busy_segments(faces: Iterable[Blockface]) -> list[tuple[Point, Point]]:
    segments: list[tuple[Point, Point]] = []
    for face in faces:
        if face.highway in BUSY_HIGHWAY_CLASSES:
            for chain in face.path:
                for a, b in zip(chain, chain[1:]):
                    segments.append((a, b))
    return segments


def _turn_angle_deg(a: Point, b: Point, c: Point) -> float:
    """Deviation from straight-on at b, in degrees; 0 means carrying on."""
    cos_lat = math.cos(math.radians(b[0]))
    v1 = ((b[1] - a[1]) * cos_lat, b[0] - a[0])
    v2 = ((c[1] - b[1]) * cos_lat, c[0] - b[0])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-12 or n2 < 1e-12:
        return 0.0
    dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _segments_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """Proper intersection of two short segments, in a flat local frame."""
    cos_lat = math.cos(math.radians(p1[0]))

    def flat(p: Point) -> tuple[float, float]:
        return (p[1] * cos_lat, p[0])

    a, b, c, d = flat(p1), flat(p2), flat(q1), flat(q2)

    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0
