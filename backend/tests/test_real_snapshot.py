from __future__ import annotations

import pytest

from app.coverage import build_coverage_report
from app.geocode import LocalGeocoder, normalise_street
from app.osm.boundary import haversine_m, ring_is_closed

pytestmark = pytest.mark.snapshot

MELBOURNE_CBD = (-37.8136, 144.9631)
SYDNEY = (-33.8688, 151.2093)


def test_snapshot_is_the_kew_district(real_snapshot):
    assert "Kew" in real_snapshot.district_name
    assert real_snapshot.relation_id == 15624487


def test_district_boundary_closes(real_snapshot):
    assert real_snapshot.rings
    assert sum(ring_is_closed(r) for r in real_snapshot.rings) >= 1


def test_district_extent_is_about_five_by_ten_kilometres(real_snapshot):
    south, west, north, east = real_snapshot.bbox
    assert haversine_m((south, west), (north, west)) == pytest.approx(5_200, rel=0.25)
    assert haversine_m((south, west), (south, east)) == pytest.approx(9_600, rel=0.25)


def test_door_count_matches_the_measured_district_total(real_snapshot):
    assert 25_000 < len(real_snapshot.addresses) < 32_000


def test_every_address_has_a_street_name(real_snapshot):
    assert [a for a in real_snapshot.addresses if not a.street] == []


def test_every_address_falls_inside_the_district_polygon(real_snapshot):
    sample = real_snapshot.addresses[::250]
    outside = [a.label for a in sample if not real_snapshot.contains(a.point)]
    assert len(outside) <= len(sample) * 0.01, f"unexpectedly outside: {outside[:5]}"


def test_points_outside_the_district_are_rejected(real_snapshot):
    assert real_snapshot.contains(SYDNEY) is False
    assert real_snapshot.contains(MELBOURNE_CBD) is False


def test_walkable_network_is_present_and_excludes_motorways(real_snapshot):
    highways = {w.highway for w in real_snapshot.ways}
    assert len(real_snapshot.ways) > 3_000
    assert "residential" in highways and "footway" in highways
    assert not {"motorway", "motorway_link", "trunk"} & highways


def test_street_names_are_spelled_out_not_abbreviated(real_snapshot):
    abbreviated = [
        a.street
        for a in real_snapshot.addresses[::100]
        if a.street.split()[-1] in {"St", "Rd", "Ave", "Cres", "Ct", "Dr", "Pde"}
    ]
    assert abbreviated == []


def test_every_address_round_trips_through_the_geocoder(real_snapshot):
    geocoder = LocalGeocoder(real_snapshot.addresses)
    for address in real_snapshot.addresses[::250]:
        candidates = geocoder.search(f"{address.number} {address.street}")
        assert candidates, f"failed to geocode {address.label}"
        assert all(c.match_type == "exact" for c in candidates), address.label
        closest = min(haversine_m((c.lat, c.lon), address.point) for c in candidates)
        assert closest < 150, f"{address.label} best candidate was {closest:.0f} m away"


def test_duplicate_street_names_yield_multiple_distinguishable_candidates(real_snapshot):
    """The district contains two Mary Streets 5.8 km apart; both must be offered."""
    geocoder = LocalGeocoder(real_snapshot.addresses)
    candidates = geocoder.search("Mary Street")
    assert len(candidates) >= 2
    assert len({c.label for c in candidates}) == len(candidates)
    spread = max(
        haversine_m((a.lat, a.lon), (b.lat, b.lon)) for a in candidates for b in candidates
    )
    assert spread > 3_000


def test_no_candidate_merges_addresses_that_are_kilometres_apart(real_snapshot):
    geocoder = LocalGeocoder(real_snapshot.addresses)
    for street in ("Mary Street", "Henry Street", "Clyde Street", "Wills Street"):
        for candidate in geocoder.search(street):
            members = [
                a
                for a in real_snapshot.addresses
                if a.street == candidate.street
                and haversine_m((candidate.lat, candidate.lon), a.point) < 400
            ]
            assert members, f"{candidate.label} has no addresses near its own centroid"


def test_abbreviated_input_with_a_reversed_suburb_name_still_resolves(real_snapshot):
    """The 'North Balwyn' / 'Balwyn North' case, against real district data."""
    geocoder = LocalGeocoder(real_snapshot.addresses)
    address = next(a for a in real_snapshot.addresses if a.street.endswith(" Street"))
    plain = geocoder.search(f"{address.number} {address.street}")
    abbreviated = geocoder.search(
        f"{address.number} {address.street.removesuffix(' Street')} St North Balwyn"
    )
    assert plain and abbreviated
    assert (plain[0].lat, plain[0].lon) == (abbreviated[0].lat, abbreviated[0].lon)


def test_known_large_unit_blocks_are_detected(real_snapshot):
    report = build_coverage_report(real_snapshot)
    assert report.gated_complex_candidates > 0
    assert report.multi_unit_stops > 100
    assert report.stops < report.doors


def test_coverage_report_has_no_missing_streets(real_snapshot):
    report = build_coverage_report(real_snapshot)
    assert report.addresses_missing_street == 0
    assert report.streets > 300


def test_geocoder_indexes_every_distinct_street(real_snapshot):
    geocoder = LocalGeocoder(real_snapshot.addresses)
    distinct = {normalise_street(a.street) for a in real_snapshot.addresses}
    assert geocoder.street_count == len(distinct)


@pytest.fixture(scope="module")
def real_stops(real_snapshot):
    from app.stops import build_stops

    return build_stops(real_snapshot.addresses)


@pytest.fixture(scope="module")
def real_blockfaces(real_snapshot, real_stops):
    from app.blockface import build_blockfaces

    return build_blockfaces(real_stops, real_snapshot.ways)


class TestRealStops:
    def test_doors_collapse_to_the_measured_stop_count(self, real_snapshot, real_stops):
        assert len(real_stops) == pytest.approx(24_366, rel=0.05)
        assert len(real_stops) < len(real_snapshot.addresses)

    def test_no_door_is_lost_or_double_counted(self, real_snapshot, real_stops):
        assigned = [d.osm_id for s in real_stops for d in s.doors]
        assert len(assigned) == len(real_snapshot.addresses)
        assert len(set(assigned)) == len(assigned)

    def test_stop_ids_are_unique_across_the_district(self, real_stops):
        assert len({s.stop_id for s in real_stops}) == len(real_stops)

    def test_the_largest_known_tower_is_one_stop(self, real_stops):
        """378 Cotham Road: 95 doors behind a single lobby."""
        cotham = [s for s in real_stops if s.street == "Cotham Road" and s.number == "378"]
        assert len(cotham) == 1
        assert cotham[0].door_count == 95
        assert cotham[0].is_gated_candidate

    def test_the_tower_is_charged_a_lobby_visit_not_ninety_five_walk_ups(self, real_stops):
        cotham = next(s for s in real_stops if s.street == "Cotham Road" and s.number == "378")
        assert cotham.dwell_minutes == pytest.approx(10.5, abs=0.1)
        # The honest full figure survives alongside the planning one.
        assert cotham.uncapped_dwell_seconds / 3600 == pytest.approx(2.0, abs=0.05)

    def test_a_second_known_unit_block_is_one_stop(self, real_stops):
        kireep = [s for s in real_stops if s.street == "Kireep Road" and s.number == "2A"]
        assert len(kireep) == 1
        assert kireep[0].door_count == 25

    def test_a_stop_never_spreads_further_than_a_single_building(self, real_stops):
        """Multi-unit stops span at most 147 m in this district; a wider one
        means two same-named streets have been merged."""
        for stop in real_stops:
            if stop.door_count < 2:
                continue
            spread = max(haversine_m(stop.point, d.point) for d in stop.doors)
            assert spread < 200, f"{stop.label} spans {spread:.0f} m"

    def test_the_two_mary_streets_never_share_a_stop(self, real_stops):
        mary = [s for s in real_stops if s.street == "Mary Street"]
        assert len(mary) > 1
        assert max(haversine_m(a.point, b.point) for a in mary for b in mary) > 3_000


class TestRealBlockfaces:
    def test_every_stop_belongs_to_exactly_one_blockface(self, real_stops, real_blockfaces):
        assigned = [s.stop_id for b in real_blockfaces for s in b.stops]
        assert len(assigned) == len(real_stops)
        assert set(assigned) == {s.stop_id for s in real_stops}

    def test_every_door_is_still_accounted_for(self, real_snapshot, real_blockfaces):
        assert sum(b.door_count for b in real_blockfaces) == len(real_snapshot.addresses)

    def test_blockface_ids_are_unique(self, real_blockfaces):
        assert len({b.blockface_id for b in real_blockfaces}) == len(real_blockfaces)

    def test_no_blockface_is_empty(self, real_blockfaces):
        assert all(b.stop_count > 0 for b in real_blockfaces)

    def test_the_district_breaks_into_a_workable_number_of_runs(self, real_blockfaces):
        assert 1_500 < len(real_blockfaces) < 5_000

    def test_a_blockface_is_a_sensible_slice_of_one_session(self, real_blockfaces):
        """A pair works ~180 minutes; a unit the router cannot split must fit."""
        minutes = sorted(b.minutes for b in real_blockfaces)
        median = minutes[len(minutes) // 2]
        assert 5 < median < 30, f"median blockface is {median:.0f} min"
        assert max(minutes) < 60, f"largest blockface is {max(minutes):.0f} min"

    def test_a_blockface_never_mixes_two_streets(self, real_blockfaces):
        for face in real_blockfaces:
            assert len({s.street for s in face.stops}) == 1

    def test_a_blockface_never_spans_the_district(self, real_blockfaces):
        """A run a pair walks in one go cannot be kilometres end to end."""
        for face in real_blockfaces:
            spread = max(
                haversine_m(face.stops[0].point, s.point) for s in face.stops
            )
            assert spread < 1_000, f"{face.label} spans {spread:.0f} m"

    def test_one_side_per_pass_blockfaces_hold_a_single_parity(self, real_blockfaces):
        from app.blockface import parity

        for face in real_blockfaces:
            if not face.one_side_per_pass:
                continue
            assert {parity(s.number) for s in face.stops} == {face.side}

    def test_arterials_are_marked_one_side_per_pass(self, real_blockfaces):
        """Nobody should be sent back and forth across Doncaster Road."""
        arterials = [
            b for b in real_blockfaces if b.street in ("Doncaster Road", "Cotham Road", "Burke Road")
        ]
        assert arterials
        assert all(b.one_side_per_pass for b in arterials)

    def test_quiet_residential_streets_are_walked_both_sides_at_once(self, real_blockfaces):
        quiet = [b for b in real_blockfaces if b.highway == "residential" and not b.off_network]
        both = [b for b in quiet if b.side == "both"]
        assert len(both) / len(quiet) > 0.9

    def test_the_two_mary_streets_never_share_a_blockface(self, real_blockfaces):
        """The Phase 1 carry-over, at blockface level."""
        for face in real_blockfaces:
            if face.street != "Mary Street":
                continue
            spread = max(haversine_m(face.stops[0].point, s.point) for s in face.stops)
            assert spread < 1_000

    def test_almost_every_stop_matches_real_street_geometry(self, real_blockfaces):
        """15 of 774 streets have addresses but no named way in the extract."""
        off = sum(b.door_count for b in real_blockfaces if b.off_network)
        total = sum(b.door_count for b in real_blockfaces)
        assert off / total < 0.02

    def test_house_numbers_run_in_order_along_a_blockface(self, real_blockfaces):
        from app.stops import sort_key

        for face in real_blockfaces:
            keys = [sort_key(s.number) for s in face.stops]
            assert keys == sorted(keys)

    def test_a_known_street_splits_at_its_cross_streets(self, real_blockfaces):
        """Cotham Road runs the width of Kew and must not be one blockface."""
        cotham = [b for b in real_blockfaces if b.street == "Cotham Road"]
        assert len(cotham) > 10


@pytest.fixture
def real_walk_graph(real_snapshot):
    from app.walkgraph import WalkGraph

    return WalkGraph(real_snapshot.ways)


def test_walking_graph_is_one_connected_footpath_network(real_walk_graph):
    import networkx as nx

    largest = max(nx.connected_components(real_walk_graph.graph), key=len)
    assert len(largest) / real_walk_graph.node_count > 0.95


def test_nearly_every_door_snaps_to_a_nearby_footpath(real_snapshot, real_walk_graph):
    snaps = real_walk_graph.snap_addresses(real_snapshot.addresses)
    assert len(snaps) / len(real_snapshot.addresses) > 0.99
    offsets = sorted(s.offset_m for s in snaps.values())
    assert offsets[len(offsets) // 2] < 25


def test_route_across_the_eastern_freeway_uses_a_real_crossing(real_walk_graph):
    """15 Aquila Street and 49 Riverside Avenue face each other across the
    Eastern Freeway, 272 m apart as the crow flies. The walking route must
    detour to a real crossing rather than cutting straight over the freeway."""
    aquila = (-37.7868078, 145.0721409)
    riverside = (-37.789239, 145.0717829)
    route = real_walk_graph.route(aquila, riverside)
    assert route is not None
    crow_flies = haversine_m(aquila, riverside)
    assert route.distance_m > 2 * crow_flies
    assert route.distance_m < 6 * crow_flies


def test_neighbouring_doors_on_one_street_route_almost_directly(real_walk_graph):
    a = (-37.7892515, 145.0714063)  # 45 Riverside Avenue
    b = (-37.789239, 145.0717829)  # 49 Riverside Avenue
    route = real_walk_graph.route(a, b)
    assert route is not None
    assert route.distance_m < 3 * haversine_m(a, b) + 50


TERRITORY_HUB = (-37.8071644, 145.0320872)  # 50 Cotham Road, near Kew Junction


@pytest.fixture(scope="module")
def hub_faces(real_snapshot, real_blockfaces):
    from app.walkgraph import WalkGraph

    graph = WalkGraph(real_snapshot.ways)
    snaps = graph.snap_addresses(real_snapshot.addresses)
    walk_m = graph.distances_from(TERRITORY_HUB, snaps, 800)
    reachable = {
        s.stop_id
        for b in real_blockfaces
        for s in b.stops
        if any(d.osm_id in walk_m for d in s.doors)
    }
    return [b for b in real_blockfaces if any(s.stop_id in reachable for s in b.stops)]


class TestRealTerritories:
    """Phase 4 against the real district: an 800 m hub near Kew Junction."""

    def test_the_hub_reaches_a_meaningful_slice_of_the_district(self, hub_faces):
        assert 100 < len(hub_faces) < 400

    def test_every_team_count_assigns_each_blockface_exactly_once(self, hub_faces):
        from app.territory import build_territories

        all_ids = sorted(b.blockface_id for b in hub_faces)
        for teams in range(1, 9):
            plan = build_territories(hub_faces, TERRITORY_HUB, teams)
            assigned = sorted(
                b.blockface_id for t in plan.territories for b in t.blockfaces
            )
            assert assigned == all_ids

    def test_teams_come_out_practically_balanced(self, hub_faces):
        from app.territory import build_territories

        for teams in range(2, 9):
            plan = build_territories(hub_faces, TERRITORY_HUB, teams)
            assert plan.spread_pct <= 0.15, (
                f"{teams} teams spread {plan.spread_pct:.0%}: "
                f"{[round(t.minutes) for t in plan.territories]}"
            )

    def test_every_territory_is_contiguous(self, hub_faces):
        from app.territory import build_territories

        for teams in range(1, 9):
            plan = build_territories(hub_faces, TERRITORY_HUB, teams)
            assert all(t.contiguous for t in plan.territories), (
                f"{teams} teams: {[t.contiguous for t in plan.territories]}"
            )

    def test_no_street_needs_splitting_at_this_radius(self, hub_faces):
        from app.territory import build_territories

        plan = build_territories(hub_faces, TERRITORY_HUB, 8)
        assert plan.split_streets == []

    def test_partitioning_is_deterministic(self, hub_faces):
        from app.territory import build_territories

        first = build_territories(hub_faces, TERRITORY_HUB, 4)
        second = build_territories(hub_faces, TERRITORY_HUB, 4)
        assert [
            [b.blockface_id for b in t.blockfaces] for t in first.territories
        ] == [[b.blockface_id for b in t.blockfaces] for t in second.territories]
