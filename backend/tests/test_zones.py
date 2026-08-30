from __future__ import annotations

import pytest

from app.blockface import build_blockfaces
from app.osm.snapshot import Address, WalkWay
from app.stops import build_stops
from app.zones import (
    PALETTE_SIZE,
    ZONE_ADJACENCY_M,
    build_zones,
    palette_indexes,
)

# A 6 x 6 lattice of short streets, ~120 m apart, each with a handful of
# houses. Big enough to cut several ways, small enough to reason about.
LATTICE_STREETS = 6
HOUSES_PER_STREET = 6
_LAT0, _LON0 = -37.8100, 145.0400
_STEP = 0.0011  # ~120 m


def _lattice() -> tuple[list[Address], list[WalkWay]]:
    addresses, ways = [], []
    for row in range(LATTICE_STREETS):
        lat = _LAT0 + row * _STEP
        west, east = _LON0, _LON0 + _STEP * (LATTICE_STREETS - 1)
        ways.append(
            WalkWay(
                osm_id=row + 1,
                geometry=[(lat, west), (lat, east)],
                tags={"highway": "residential", "name": f"Row {row} Street", "maxspeed": "50"},
            )
        )
        for house in range(HOUSES_PER_STREET):
            frac = (house + 0.5) / HOUSES_PER_STREET
            addresses.append(
                Address(
                    osm_id=f"r{row}h{house}",
                    lat=lat + 0.00015,
                    lon=west + (east - west) * frac,
                    number=str(house + 1),
                    street=f"Row {row} Street",
                )
            )
    return addresses, ways


@pytest.fixture(scope="module")
def lattice_faces():
    addresses, ways = _lattice()
    return build_blockfaces(build_stops(addresses), ways)


class TestSizing:
    def test_every_door_is_placed_or_explicitly_dropped(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=12)
        assert plan.covered_doors + plan.dropped_doors == plan.total_doors
        assert plan.total_doors == LATTICE_STREETS * HOUSES_PER_STREET

    def test_no_blockface_lands_in_two_zones(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=12)
        ids = [b.blockface_id for z in plan.zones for b in z.blockfaces]
        assert len(ids) == len(set(ids))

    def test_a_bigger_target_makes_fewer_zones(self, lattice_faces):
        small = build_zones(lattice_faces, target_doors=6)
        large = build_zones(lattice_faces, target_doors=18)
        assert len(small.zones) > len(large.zones)

    def test_zones_come_out_near_the_target(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=12)
        for zone in plan.zones:
            assert 6 <= zone.door_count <= 24, zone.label

    def test_a_target_larger_than_the_district_makes_one_zone(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=10_000)
        assert len(plan.zones) == 1
        assert plan.zones[0].door_count == plan.total_doors

    def test_a_target_of_one_still_terminates(self, lattice_faces):
        """The recursion must stop on single blockfaces, not split forever."""
        plan = build_zones(lattice_faces, target_doors=1)
        assert plan.zones
        assert all(z.blockfaces for z in plan.zones)

    def test_a_zero_target_is_rejected(self, lattice_faces):
        with pytest.raises(ValueError):
            build_zones(lattice_faces, target_doors=0)

    def test_no_blockface_is_ever_cut_in_half(self, lattice_faces):
        """A run of houses a pair walks in one go stays in one zone."""
        plan = build_zones(lattice_faces, target_doors=7)
        placed = {b.blockface_id for z in plan.zones for b in z.blockfaces}
        dropped = {b.blockface_id for b in plan.dropped_blockfaces}
        assert placed | dropped == {b.blockface_id for b in lattice_faces}
        assert not (placed & dropped)


class TestShape:
    def test_zones_are_compact_rather_than_strung_out(self, lattice_faces):
        """A KD cut should give patches, not ribbons across the whole lattice."""
        plan = build_zones(lattice_faces, target_doors=12)
        whole = build_zones(lattice_faces, target_doors=10_000).zones[0]
        south, west, north, east = whole.bbox
        for zone in plan.zones:
            zs, zw, zn, ze = zone.bbox
            assert (zn - zs) <= (north - south) * 0.75 or (ze - zw) <= (east - west) * 0.75

    def test_cuts_alternate_axis_instead_of_slicing_one_way(self, lattice_faces):
        """Degrees of longitude are ~140x shorter than latitude here, so an
        axis chosen in raw degrees would cut every group the same way."""
        plan = build_zones(lattice_faces, target_doors=9)
        boxes = [z.bbox for z in plan.zones]
        spans_lat = {round(b[2] - b[0], 5) for b in boxes}
        spans_lon = {round(b[3] - b[1], 5) for b in boxes}
        assert len(spans_lat) > 1 or len(spans_lon) > 1

    def test_zone_ids_are_unique_and_ordered_from_the_south(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=12)
        ids = [z.zone_id for z in plan.zones]
        assert len(set(ids)) == len(ids)
        assert ids == sorted(ids)
        assert [z.centroid[0] for z in plan.zones] == sorted(
            z.centroid[0] for z in plan.zones
        )

    def test_partitioning_is_deterministic(self, lattice_faces):
        first = build_zones(lattice_faces, target_doors=11)
        second = build_zones(lattice_faces, target_doors=11)
        assert [[b.blockface_id for b in z.blockfaces] for z in first.zones] == [
            [b.blockface_id for b in z.blockfaces] for z in second.zones
        ]


class TestConnectivity:
    def test_a_stranded_pocket_is_dropped_not_silently_kept(self):
        """One house far from everything cannot be walked to from its zone."""
        addresses, ways = _lattice()
        addresses.append(
            Address(osm_id="far", lat=-37.8600, lon=145.0400, number="1", street="Marooned Road")
        )
        faces = build_blockfaces(build_stops(addresses), ways)
        plan = build_zones(faces, target_doors=10_000)
        assert plan.dropped_doors == 1
        assert "Marooned Road" in {b.street for b in plan.dropped_blockfaces}
        assert plan.coverage_pct < 1.0

    def test_the_zone_keeps_the_side_holding_the_work(self):
        addresses, ways = _lattice()
        addresses.append(
            Address(osm_id="far", lat=-37.8600, lon=145.0400, number="1", street="Marooned Road")
        )
        faces = build_blockfaces(build_stops(addresses), ways)
        [zone] = build_zones(faces, target_doors=10_000).zones
        assert zone.door_count == LATTICE_STREETS * HOUSES_PER_STREET
        assert zone.dropped_doors == 1

    def test_a_fully_connected_district_drops_nothing(self, lattice_faces):
        plan = build_zones(lattice_faces, target_doors=12)
        assert plan.dropped_doors == 0
        assert plan.coverage_pct == 1.0

    def test_neighbouring_blockfaces_within_the_adjacency_radius_stay_together(self):
        """Two runs closer than the radius must never be called separate."""
        addresses, ways = _lattice()
        faces = build_blockfaces(build_stops(addresses), ways)
        plan = build_zones(faces, target_doors=10_000)
        assert len(plan.zones) == 1
        assert _STEP * 111_320 < ZONE_ADJACENCY_M, "fixture assumption"


class TestReporting:
    def test_streets_spanning_two_zones_are_reported(self):
        """A street longer than the target cannot fit in one zone; say so.

        Needs a street with more than one blockface, so the lattice (whose
        rows are uncrossed and therefore single blockfaces) will not do: this
        builds one long street cut in two by a cross street.
        """
        long_street = WalkWay(
            osm_id=1,
            geometry=[(-37.81, 145.040), (-37.81, 145.045), (-37.81, 145.050)],
            tags={"highway": "residential", "name": "Long Street", "maxspeed": "50"},
        )
        cross = WalkWay(
            osm_id=2,
            geometry=[(-37.81, 145.045), (-37.808, 145.045)],
            tags={"highway": "residential", "name": "Cross Street", "maxspeed": "50"},
        )
        houses = [
            Address(
                osm_id=f"h{i}",
                lat=-37.80985,
                lon=145.0405 + i * 0.0005,
                number=str(i + 1),
                street="Long Street",
            )
            for i in range(18)
        ]
        faces = build_blockfaces(build_stops(houses), [long_street, cross])
        assert len(faces) > 1, "fixture assumption: the cross street splits it"
        plan = build_zones(faces, target_doors=6)
        assert len(plan.zones) > 1
        assert plan.split_streets == ["Long Street"]

    def test_a_single_zone_splits_no_street(self, lattice_faces):
        assert build_zones(lattice_faces, target_doors=10_000).split_streets == []

    def test_payload_reports_coverage_and_spread(self, lattice_faces):
        payload = build_zones(lattice_faces, target_doors=12).to_dict()
        assert payload["zone_count"] > 1
        assert payload["coverage_pct"] == 100.0
        assert payload["size_spread_pct"] >= 0
        assert payload["target_doors"] == 12

    def test_a_zone_label_names_its_streets_and_size(self, lattice_faces):
        zone = build_zones(lattice_faces, target_doors=12).zones[0]
        assert "doors" in zone.label
        assert zone.streets
        assert zone.to_dict()["bbox"][0] <= zone.to_dict()["bbox"][2]


class TestPalette:
    def test_touching_zones_never_share_a_colour(self, lattice_faces):
        zones = build_zones(lattice_faces, target_doors=8).zones
        colours = palette_indexes(zones)
        from app.zones import _boxes_touch

        for i, a in enumerate(zones):
            for b in zones[i + 1 :]:
                if _boxes_touch(a.bbox, b.bbox):
                    assert colours[a.zone_id] != colours[b.zone_id], f"{a.label} / {b.label}"

    def test_every_zone_gets_a_colour_in_range(self, lattice_faces):
        zones = build_zones(lattice_faces, target_doors=8).zones
        colours = palette_indexes(zones)
        assert set(colours) == {z.zone_id for z in zones}
        assert all(0 <= c < PALETTE_SIZE for c in colours.values())

    def test_the_palette_is_never_wider_than_the_map_can_show(self, lattice_faces):
        """`PALETTE_SIZE` has to match the colour list in MapView.tsx, or a
        zone gets the fallback grey and stops being distinguishable."""
        zones = build_zones(lattice_faces, target_doors=8).zones
        assert max(palette_indexes(zones).values()) < PALETTE_SIZE
