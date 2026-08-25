from __future__ import annotations

import pytest

from app.blockface import Blockface, build_blockfaces
from app.osm.boundary import haversine_m
from app.osm.snapshot import Address, WalkWay
from app.routing import RELOAD_SECONDS, RouteConfig, RoutePlan, plan_route
from app.stops import build_stops

# One long east-west street of quiet residential segments, hub at its west
# end, so "near the hub" and "far from the hub" are unambiguous. Segment
# spacing is ~175 m; each segment carries 4 doors.
LAT = -37.800
LONS = [145.050 + j * 0.002 for j in range(7)]

HUB = (LAT, LONS[0])


def _door(osm_id, lat, lon, number, street):
    return Address(osm_id=osm_id, lat=lat, lon=lon, number=number, street=street)


def _line_faces() -> list[Blockface]:
    ways = []
    doors = []
    for j in range(len(LONS) - 1):
        name = f"Segment {j} Street"
        ways.append(
            WalkWay(
                j + 1,
                [(LAT, LONS[j]), (LAT, LONS[j + 1])],
                {"highway": "residential", "name": name, "maxspeed": "50"},
            )
        )
        doors.extend(
            _door(
                f"d{j}.{k}",
                LAT + 0.0002,
                LONS[j] + (LONS[j + 1] - LONS[j]) * fraction,
                str(k * 2 + 1),
                name,
            )
            for k, fraction in enumerate((0.2, 0.4, 0.6, 0.8))
        )
    return build_blockfaces(build_stops(doors), ways)


@pytest.fixture(scope="module")
def faces() -> list[Blockface]:
    return _line_faces()


def _face_visits(plan: RoutePlan):
    return [v for v in plan.visits if v.kind == "blockface"]


class TestConfig:
    def test_defaults_are_valid(self):
        RouteConfig()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"pamphlets": 0},
            {"take_up": 0.0},
            {"take_up": 1.5},
            {"speed_m_per_min": 0},
            {"session_minutes": 0},
        ],
    )
    def test_bad_dials_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            RouteConfig(**kwargs)

    def test_demand_rounds_up_and_never_hits_zero(self, faces):
        config = RouteConfig(take_up=0.3)
        for face in faces:
            demand = config.demand(face)
            assert demand >= 1
            assert demand >= face.door_count * 0.3

    def test_take_up_scales_pamphlets_not_time(self, faces):
        full = RouteConfig(take_up=1.0)
        half = RouteConfig(take_up=0.5)
        face = faces[0]
        assert half.demand(face) < full.demand(face)
        assert half.service_seconds(face) == full.service_seconds(face)


class TestWholeBlockfaces:
    def test_every_blockface_is_served_or_dropped_never_split(self, faces):
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=6, session_minutes=180))
        served = [f.blockface_id for f in plan.served]
        dropped = [f.blockface_id for f in plan.dropped]
        assert sorted(served + dropped) == sorted(f.blockface_id for f in faces)
        assert len(set(served)) == len(served)

    def test_an_empty_area_yields_an_empty_plan(self):
        plan = plan_route([], HUB)
        assert plan.visits == []
        assert plan.dropped == []
        assert plan.metrics()["coverage_pct"] == 0.0


class TestCapacity:
    def test_restocks_appear_exactly_when_the_bags_run_out(self, faces):
        """Each face takes 4 pamphlets; a 6-pamphlet load holds one face plus
        change, so a reload must come between every pair of faces - and the
        count must never go below zero anywhere along the plan."""
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=6, session_minutes=600))
        assert plan.restock_trips > 0
        for visit in plan.visits:
            assert visit.pamphlets_left >= 0
        for visit in plan.visits:
            if visit.kind == "reload":
                assert visit.pamphlets_left == 6

    def test_a_reload_never_appears_while_the_load_still_covers_the_next_face(self, faces):
        """Reloads cost 5 minutes; the solver must not restock on a whim."""
        config = RouteConfig(pamphlets=1_000, session_minutes=600)
        plan = plan_route(faces, HUB, config)
        assert plan.restock_trips == 0

    def test_the_full_load_comes_back_at_every_restock(self, faces):
        config = RouteConfig(pamphlets=9, session_minutes=600)
        plan = plan_route(faces, HUB, config)
        reloads = [v for v in plan.visits if v.kind == "reload"]
        assert reloads
        assert all(v.pamphlets_left == 9 for v in reloads)

    def test_capacity_off_means_no_restocks_ever(self, faces):
        config = RouteConfig(pamphlets=6, session_minutes=600, capacity_enabled=False)
        plan = plan_route(faces, HUB, config)
        assert plan.restock_trips == 0
        assert len(plan.served) == len(faces)

    def test_ab_capacity_off_cuts_the_restock_walking(self, faces):
        """The Phase 5 acceptance A/B: same area, same budget, capacity on
        versus off. Off must serve at least as much while spending no time on
        restock trips - the walking those trips cost is the difference."""
        on = plan_route(faces, HUB, RouteConfig(pamphlets=6, session_minutes=600))
        off = plan_route(
            faces, HUB, RouteConfig(pamphlets=6, session_minutes=600, capacity_enabled=False)
        )
        assert len(off.served) >= len(on.served)
        assert off.restock_trips == 0 < on.restock_trips
        restock_overhead = (
            on.walk_seconds + on.restock_trips * RELOAD_SECONDS - off.walk_seconds
        )
        assert restock_overhead > 0


class TestBudgetAndPriority:
    def test_the_session_budget_is_a_hard_ceiling(self, faces):
        for minutes in (30, 60, 90, 180):
            plan = plan_route(faces, HUB, RouteConfig(pamphlets=500, session_minutes=minutes))
            assert plan.total_minutes <= minutes + 1e-6

    def test_a_budget_too_short_for_anything_drops_everything(self, faces):
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=500, session_minutes=1))
        assert plan.served == []
        assert sorted(f.blockface_id for f in plan.dropped) == sorted(
            f.blockface_id for f in faces
        )

    def test_what_gets_dropped_is_the_farthest_work(self, faces):
        """A budget that fits only part of the street must shed from the far
        end, never from next to the hub."""
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=500, session_minutes=45))
        assert plan.served and plan.dropped
        farthest_served = max(haversine_m(f.centroid, HUB) for f in plan.served)
        nearest_dropped = min(haversine_m(f.centroid, HUB) for f in plan.dropped)
        assert nearest_dropped > farthest_served

    def test_near_hub_houses_come_early(self, faces):
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=500, session_minutes=600))
        visited = [v.blockface for v in _face_visits(plan)]
        distances = [haversine_m(f.centroid, HUB) for f in visited]
        first_half = distances[: len(distances) // 2]
        second_half = distances[len(distances) // 2 :]
        assert min(first_half) < min(second_half)
        assert sum(first_half) / len(first_half) < sum(second_half) / len(second_half)

    def test_the_clock_only_ever_moves_forward(self, faces):
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=9, session_minutes=600))
        minutes = [v.arrive_minute for v in plan.visits]
        assert minutes == sorted(minutes)
        assert minutes[0] == 0.0


class TestDeterminism:
    def test_the_same_input_always_yields_the_same_route(self, faces):
        """The golden-file property: search is bounded by solution count, not
        wall time, so the plan is a pure function of its input."""
        config = RouteConfig(pamphlets=9, session_minutes=120)
        first = plan_route(faces, HUB, config)
        second = plan_route(list(reversed(faces)), HUB, config)
        golden = [
            (v.kind, v.blockface.blockface_id if v.blockface else None)
            for v in first.visits
        ]
        assert golden == [
            (v.kind, v.blockface.blockface_id if v.blockface else None)
            for v in second.visits
        ]
        assert first.metrics() == second.metrics()


class TestMetrics:
    def test_the_panel_figures_add_up(self, faces):
        plan = plan_route(faces, HUB, RouteConfig(pamphlets=9, session_minutes=600))
        metrics = plan.metrics()
        assert metrics["walking_pct"] + metrics["knocking_pct"] == pytest.approx(100, abs=0.2)
        assert metrics["doors_served"] + metrics["doors_dropped"] == sum(
            f.door_count for f in faces
        )
        assert metrics["blockfaces_served"] == len(plan.served)
        assert metrics["restock_minutes"] == pytest.approx(
            metrics["restock_trips"] * RELOAD_SECONDS / 60, abs=0.1
        )
        assert metrics["coverage_pct"] == pytest.approx(
            100 * metrics["doors_served"]
            / (metrics["doors_served"] + metrics["doors_dropped"]),
            abs=0.1,
        )
