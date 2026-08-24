from __future__ import annotations

import pytest

from app.osm.boundary import haversine_m
from app.osm.snapshot import Address, WalkWay
from app.walkgraph import WALKING_SPEED_M_PER_MIN, WalkGraph, is_walkable

# A hand-built network: two parallel east-west streets separated by an
# uncrossable gap (a river, say), joined only by a footbridge at their
# western ends.
#
#   north street:  NW ──────────── NE
#                  |  (footbridge)
#   south street:  SW ──────────── SE
#
SW = (-37.8000, 145.0500)
SE = (-37.8000, 145.0540)
NW = (-37.7980, 145.0500)
NE = (-37.7980, 145.0540)

SOUTH_STREET = WalkWay(osm_id=1, geometry=[SW, SE], tags={"highway": "residential", "name": "South Street"})
NORTH_STREET = WalkWay(osm_id=2, geometry=[NW, NE], tags={"highway": "residential", "name": "North Street"})
FOOTBRIDGE = WalkWay(osm_id=3, geometry=[SW, NW], tags={"highway": "footway", "bridge": "yes"})

# An island way that connects to nothing.
ISLAND = WalkWay(osm_id=4, geometry=[(-37.7900, 145.0700), (-37.7900, 145.0710)], tags={"highway": "footway"})

NETWORK = [SOUTH_STREET, NORTH_STREET, FOOTBRIDGE, ISLAND]

# Doors either side of the gap, near the eastern ends of the two streets.
DOOR_SOUTH = (-37.80005, 145.0530)
DOOR_NORTH = (-37.79795, 145.0530)


@pytest.fixture
def graph() -> WalkGraph:
    return WalkGraph(NETWORK)


class TestWalkable:
    def test_ordinary_highways_are_walkable(self):
        assert is_walkable(SOUTH_STREET)
        assert is_walkable(FOOTBRIDGE)

    def test_foot_no_is_excluded_whatever_the_class(self):
        way = WalkWay(osm_id=9, geometry=[SW, SE], tags={"highway": "footway", "foot": "no"})
        assert not is_walkable(way)

    def test_private_access_is_excluded(self):
        way = WalkWay(osm_id=9, geometry=[SW, SE], tags={"highway": "service", "access": "private"})
        assert not is_walkable(way)

    def test_explicit_foot_yes_overrides_private_access(self):
        way = WalkWay(
            osm_id=9, geometry=[SW, SE], tags={"highway": "service", "access": "private", "foot": "yes"}
        )
        assert is_walkable(way)

    def test_unwalkable_ways_never_enter_the_graph(self):
        gated = WalkWay(osm_id=9, geometry=[SW, SE], tags={"highway": "service", "access": "private"})
        with_gated = WalkGraph([*NETWORK, gated])
        assert with_gated.edge_count == WalkGraph(NETWORK).edge_count


class TestGraphAssembly:
    def test_ways_sharing_a_node_connect(self, graph: WalkGraph):
        # SW appears in both the south street and the footbridge, so they
        # must be one component; the island stays separate.
        route = graph.route(DOOR_SOUTH, DOOR_NORTH)
        assert route is not None

    def test_node_and_edge_counts(self, graph: WalkGraph):
        assert graph.node_count == 6  # SW/SE/NW/NE + two island ends
        assert graph.edge_count == 4


class TestSnap:
    def test_snaps_to_nearest_edge_with_offset(self, graph: WalkGraph):
        snap = graph.snap(*DOOR_SOUTH)
        assert snap is not None
        assert {snap.a, snap.b} == {SW, SE}
        assert snap.offset_m == pytest.approx(haversine_m(DOOR_SOUTH, snap.point), abs=0.1)
        assert snap.offset_m < 10
        assert snap.to_a_m + snap.to_b_m == pytest.approx(haversine_m(SW, SE), rel=0.01)

    def test_far_from_any_way_returns_none(self, graph: WalkGraph):
        assert graph.snap(-37.9000, 145.2000) is None


class TestRoute:
    def test_same_street_route_is_the_along_street_distance(self, graph: WalkGraph):
        near_west = (-37.80005, 145.0505)
        route = graph.route(near_west, DOOR_SOUTH)
        assert route is not None
        along = haversine_m((-37.8000, 145.0505), (-37.8000, 145.0530))
        assert route.distance_m == pytest.approx(along, abs=15)
        assert route.minutes == pytest.approx(route.distance_m / WALKING_SPEED_M_PER_MIN)

    def test_gap_is_crossed_via_the_bridge_not_as_the_crow_flies(self, graph: WalkGraph):
        route = graph.route(DOOR_SOUTH, DOOR_NORTH)
        assert route is not None
        crow_flies = haversine_m(DOOR_SOUTH, DOOR_NORTH)
        assert crow_flies < 250
        # Door -> west along south street -> bridge -> east along north street.
        assert route.distance_m > 3 * crow_flies
        assert SW in route.points and NW in route.points

    def test_route_starts_and_ends_at_the_doors(self, graph: WalkGraph):
        route = graph.route(DOOR_SOUTH, DOOR_NORTH)
        assert route is not None
        assert route.points[0] == DOOR_SOUTH
        assert route.points[-1] == DOOR_NORTH

    def test_disconnected_components_are_unreachable(self, graph: WalkGraph):
        route = graph.route(DOOR_SOUTH, (-37.7900, 145.0705))
        assert route is None

    def test_unsnappable_point_is_unreachable(self, graph: WalkGraph):
        assert graph.route(DOOR_SOUTH, (-37.9000, 145.2000)) is None

    def test_routing_leaves_the_graph_unchanged(self, graph: WalkGraph):
        before = (graph.node_count, graph.edge_count)
        graph.route(DOOR_SOUTH, DOOR_NORTH)
        graph.route(DOOR_SOUTH, (-37.9000, 145.2000))
        assert (graph.node_count, graph.edge_count) == before


class TestDistancesFrom:
    def _snaps(self, graph: WalkGraph) -> dict:
        addresses = [
            Address(osm_id="south", lat=DOOR_SOUTH[0], lon=DOOR_SOUTH[1], number="1", street="South Street"),
            Address(osm_id="north", lat=DOOR_NORTH[0], lon=DOOR_NORTH[1], number="2", street="North Street"),
            Address(osm_id="island", lat=-37.7899, lon=145.0705, number="3", street="Island Walk"),
        ]
        return graph.snap_addresses(addresses)

    def test_matches_pairwise_routes(self, graph: WalkGraph):
        snaps = self._snaps(graph)
        origin = (-37.80005, 145.0510)
        distances = graph.distances_from(origin, snaps, cutoff_m=2000)
        for osm_id, door in (("south", DOOR_SOUTH), ("north", DOOR_NORTH)):
            route = graph.route(origin, door)
            assert route is not None
            assert distances[osm_id] == pytest.approx(route.distance_m, abs=1)

    def test_cutoff_drops_far_doors_and_other_components(self, graph: WalkGraph):
        snaps = self._snaps(graph)
        origin = (-37.80005, 145.0510)
        distances = graph.distances_from(origin, snaps, cutoff_m=300)
        assert "south" in distances
        assert "north" not in distances  # ~750 m via the bridge
        assert "island" not in distances  # unreachable at any cutoff

    def test_same_edge_shortcut(self, graph: WalkGraph):
        snaps = self._snaps(graph)
        # Origin on the same edge as the south door, a few metres away.
        origin = (-37.80005, 145.0528)
        distances = graph.distances_from(origin, snaps, cutoff_m=2000)
        assert distances["south"] < 30
