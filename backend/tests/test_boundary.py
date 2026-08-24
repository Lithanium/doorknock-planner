from __future__ import annotations

import pytest

from app.osm.boundary import (
    assemble_rings,
    haversine_m,
    point_in_ring,
    point_in_rings,
    ring_is_closed,
    rings_bbox,
    rings_to_geojson,
)

SQUARE = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


def test_assembles_shuffled_and_reversed_ways_into_one_closed_ring():
    ways = [
        [(1.0, 1.0), (1.0, 0.0)],
        [(0.0, 0.0), (0.0, 1.0)],
        [(1.0, 0.0), (0.0, 0.0)],
        [(0.0, 1.0), (1.0, 1.0)],
    ]
    rings = assemble_rings(ways)
    assert len(rings) == 1
    assert ring_is_closed(rings[0])
    assert {(round(lat, 6), round(lon, 6)) for lat, lon in rings[0]} == set(SQUARE)


def test_assembles_two_disjoint_rings_separately():
    far = [(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.0)]
    ways = []
    for ring in (SQUARE, far):
        ways += [[ring[i], ring[(i + 1) % 4]] for i in range(4)]
    rings = assemble_rings(ways)
    assert len(rings) == 2
    assert all(ring_is_closed(r) for r in rings)


def test_open_boundary_still_returns_a_ring_but_is_flagged_unclosed():
    rings = assemble_rings([[(0.0, 0.0), (0.0, 1.0)], [(0.0, 1.0), (1.0, 1.0)]])
    assert len(rings) == 1
    assert not ring_is_closed(rings[0])


def test_degenerate_ways_are_ignored():
    assert assemble_rings([[(0.0, 0.0)], []]) == []


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ((0.5, 0.5), True),
        ((0.01, 0.99), True),
        ((1.5, 0.5), False),
        ((-0.5, 0.5), False),
        ((0.5, 1.5), False),
    ],
)
def test_point_in_ring(point, expected):
    assert point_in_ring(point, SQUARE) is expected


def test_point_in_rings_uses_even_odd_so_holes_are_excluded():
    hole = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4)]
    assert point_in_rings((0.5, 0.5), [SQUARE, hole]) is False
    assert point_in_rings((0.2, 0.2), [SQUARE, hole]) is True


def test_rings_bbox():
    assert rings_bbox([SQUARE]) == (0.0, 0.0, 1.0, 1.0)


def test_rings_bbox_rejects_empty():
    with pytest.raises(ValueError):
        rings_bbox([])


def test_geojson_uses_lon_lat_order():
    geojson = rings_to_geojson([SQUARE])
    assert geojson["type"] == "MultiPolygon"
    assert geojson["coordinates"][0][0][1] == [1.0, 0.0]


def test_haversine_against_known_distance():
    melbourne = (-37.8136, 144.9631)
    sydney = (-33.8688, 151.2093)
    assert haversine_m(melbourne, sydney) == pytest.approx(713_000, rel=0.02)


def test_haversine_short_distance():
    assert haversine_m((-37.8000, 145.0500), (-37.8009, 145.0500)) == pytest.approx(100, rel=0.02)
