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
