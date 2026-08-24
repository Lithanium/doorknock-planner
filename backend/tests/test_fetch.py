from __future__ import annotations

import pytest

from app.osm.fetch import (
    FetchStats,
    addresses_query,
    boundary_query,
    fetch_district,
    parse_addresses,
    parse_boundary,
    parse_ways,
    ways_query,
)
from app.osm.overpass import OverpassClient
from tests.conftest import BOUNDARY_WAYS_SHUFFLED

RELATION_ID = 15624487


def _relation_element(ways):
    return {
        "type": "relation",
        "id": RELATION_ID,
        "tags": {"name": "Electoral district of Kew", "boundary": "political"},
        "members": [
            {"type": "way", "geometry": [{"lat": lat, "lon": lon} for lat, lon in way]}
            for way in ways
        ],
    }


@pytest.mark.parametrize("builder", [boundary_query, addresses_query, ways_query])
def test_queries_reference_the_relation_and_have_a_timeout(builder):
    ql = builder(RELATION_ID, 600)
    assert str(RELATION_ID) in ql
    assert "timeout:600" in ql


@pytest.mark.parametrize("builder", [addresses_query, ways_query])
def test_data_queries_clip_to_the_district_polygon_not_a_bbox(builder):
    ql = builder(RELATION_ID, 600)
    assert "map_to_area" in ql
    assert "area.d" in ql


def test_ways_query_excludes_motorways_but_keeps_streets_people_live_on():
    ql = ways_query(RELATION_ID, 600)
    assert "residential" in ql and "footway" in ql and "primary" in ql
    assert "motorway" not in ql


def test_parse_boundary_stitches_members_into_a_closed_ring():
    stats = FetchStats()
    name, rings = parse_boundary([_relation_element(BOUNDARY_WAYS_SHUFFLED)], stats)
    assert name == "Electoral district of Kew"
    assert len(rings) == 1
    assert stats.boundary_ways == 4
    assert stats.closed_rings == 1
    assert stats.warnings == []


def test_parse_boundary_warns_when_the_outline_does_not_close():
    stats = FetchStats()
    parse_boundary([_relation_element(BOUNDARY_WAYS_SHUFFLED[:2])], stats)
    assert stats.closed_rings == 0
    assert any("incomplete" in w for w in stats.warnings)


def test_parse_boundary_rejects_a_response_with_no_relation():
    with pytest.raises(ValueError, match="no relation"):
        parse_boundary([{"type": "node", "id": 1}], FetchStats())


def test_boundary_query_requests_member_geometry_not_tags_only():
    """`out tags` silently drops relation members, yielding an empty boundary."""
    ql = boundary_query(RELATION_ID, 600)
    assert "out geom;" in ql
    assert "tags" not in ql


def test_parse_boundary_fails_loudly_when_members_have_no_geometry():
    element = {
        "type": "relation",
        "id": RELATION_ID,
        "tags": {"name": "Electoral district of Kew"},
        "members": [{"type": "way", "ref": 1}],
    }
    with pytest.raises(ValueError, match="out geom"):
        parse_boundary([element], FetchStats())


def test_parse_addresses_handles_nodes_and_building_centroids():
    stats = FetchStats()
    addresses = parse_addresses(
        [
            {
                "type": "node",
                "id": 1,
                "lat": -37.8,
                "lon": 145.05,
                "tags": {"addr:housenumber": "22", "addr:street": "Yerrin Street"},
            },
            {
                "type": "way",
                "id": 2,
                "center": {"lat": -37.81, "lon": 145.06},
                "tags": {
                    "addr:housenumber": "14",
                    "addr:street": "Brenbeal Street",
                    "addr:unit": "3",
                    "addr:postcode": "3103",
                },
            },
        ],
        stats,
    )
    assert [a.osm_id for a in addresses] == ["n1", "w2"]
    assert addresses[0].point == (-37.8, 145.05)
    assert addresses[1].unit == "3"
    assert addresses[1].postcode == "3103"
    assert stats.addresses_kept == 2


def test_parse_addresses_skips_records_that_cannot_be_used():
    stats = FetchStats()
    addresses = parse_addresses(
        [
            {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0, "tags": {"addr:street": "No Number"}},
            {"type": "node", "id": 2, "lat": 0.0, "lon": 0.0, "tags": {"addr:housenumber": "5"}},
            {"type": "way", "id": 3, "tags": {"addr:housenumber": "7", "addr:street": "No Centre"}},
        ],
        stats,
    )
    assert addresses == []
    assert stats.skipped_no_street == 1
    assert stats.skipped_no_position == 1
    assert any("addr:street" in w for w in stats.warnings)


def test_parse_ways_keeps_geometry_and_whitelisted_tags_only():
    stats = FetchStats()
    ways = parse_ways(
        [
            {
                "type": "way",
                "id": 10,
                "tags": {
                    "highway": "residential",
                    "name": "Yerrin Street",
                    "source": "Vicmap",
                    "lanes": "2",
                },
                "geometry": [{"lat": -37.8, "lon": 145.05}, {"lat": -37.801, "lon": 145.051}],
            },
            {"type": "way", "id": 11, "geometry": [{"lat": -37.8, "lon": 145.05}]},
        ],
        stats,
    )
    assert len(ways) == 1
    assert ways[0].highway == "residential"
    assert ways[0].name == "Yerrin Street"
    assert "source" not in ways[0].tags
    assert ways[0].tags["lanes"] == "2"
    assert stats.ways_kept == 1


def test_fetch_district_issues_three_queries_and_builds_a_snapshot():
    responses = [
        {"elements": [_relation_element(BOUNDARY_WAYS_SHUFFLED)]},
        {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": -37.8,
                    "lon": 145.05,
                    "tags": {"addr:housenumber": "22", "addr:street": "Yerrin Street"},
                }
            ]
        },
        {
            "elements": [
                {
                    "type": "way",
                    "id": 10,
                    "tags": {"highway": "residential"},
                    "geometry": [{"lat": -37.8, "lon": 145.05}, {"lat": -37.801, "lon": 145.051}],
                }
            ]
        },
    ]
    sent: list[str] = []

    def post(_url, ql):
        import json

        sent.append(ql)
        return json.dumps(responses[len(sent) - 1]).encode()

    client = OverpassClient(mirrors=["https://a/api"], post=post, sleep=lambda _s: None)
    snapshot, stats = fetch_district(client, RELATION_ID, timeout_s=600)

    assert len(sent) == 3
    assert snapshot.district_name == "Electoral district of Kew"
    assert len(snapshot.addresses) == 1
    assert len(snapshot.ways) == 1
    assert snapshot.fetched_at.endswith("+00:00")
    assert stats.closed_rings == 1


def test_fetch_district_reports_progress():
    messages: list[str] = []
    responses = [
        {"elements": [_relation_element(BOUNDARY_WAYS_SHUFFLED)]},
        {"elements": []},
        {"elements": []},
    ]
    calls = {"n": 0}

    def post(_url, _ql):
        import json

        calls["n"] += 1
        return json.dumps(responses[calls["n"] - 1]).encode()

    client = OverpassClient(mirrors=["https://a/api"], post=post, sleep=lambda _s: None)
    fetch_district(client, RELATION_ID, progress=messages.append)
    assert any("boundary" in m for m in messages)
    assert any("addresses" in m for m in messages)
    assert any("walkable" in m for m in messages)
