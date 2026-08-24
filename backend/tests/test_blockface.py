from __future__ import annotations

import pytest

from app.blockface import (
    BOTH,
    EVEN,
    MAX_BLOCKFACE_MINUTES,
    ODD,
    Blockface,
    StreetNetwork,
    build_blockfaces,
    is_busy,
    parity,
    split_into_parts,
)
from app.osm.snapshot import Address, WalkWay
from app.stops import build_stops

# A hand-built grid. Quiet Street runs east-west and is crossed by Cross
# Street in the middle, so it is two blocks. Busy Road runs parallel to the
# south and is a 4-lane primary, so its two sides never share a blockface.
#
#   quiet street:  QW ───── QM ───── QE      (crossed at QM by Cross Street)
#                            |
#   busy road:     BW ───── BM ───── BE
#
QW = (-37.8000, 145.0500)
QM = (-37.8000, 145.0520)
QE = (-37.8000, 145.0540)
BW = (-37.8020, 145.0500)
BM = (-37.8020, 145.0520)
BE = (-37.8020, 145.0540)

QUIET_WEST = WalkWay(1, [QW, QM], {"highway": "residential", "name": "Quiet Street", "maxspeed": "50"})
QUIET_EAST = WalkWay(2, [QM, QE], {"highway": "residential", "name": "Quiet Street", "maxspeed": "50"})
CROSS = WalkWay(3, [QM, BM], {"highway": "residential", "name": "Cross Street", "maxspeed": "50"})
BUSY = WalkWay(
    4, [BW, BM, BE], {"highway": "primary", "name": "Busy Road", "lanes": "4", "maxspeed": "60"}
)
DRIVEWAY = WalkWay(5, [QW, (-37.7995, 145.0500)], {"highway": "service"})

# A V-shaped street, used to prove two spans that share only their junction
# node still get distinct ids.
VW, VBOTTOM, VE = (-37.7980, 145.0500), (-37.8000, 145.0520), (-37.7980, 145.0540)

WAYS = [QUIET_WEST, QUIET_EAST, CROSS, BUSY, DRIVEWAY]


def _door(osm_id: str, lat: float, lon: float, number: str, street: str, unit=None) -> Address:
    return Address(osm_id=osm_id, lat=lat, lon=lon, number=number, street=street, unit=unit)


def _along(a, b, fraction, offset_deg=0.0002):
    """A door set back `offset_deg` north of the point `fraction` along a-b."""
    return (a[0] + (b[0] - a[0]) * fraction + offset_deg, a[1] + (b[1] - a[1]) * fraction)


# Quiet Street: #1-#4 on the western block, #5-#8 on the eastern block.
QUIET_DOORS = [
    _door("q1", *_along(QW, QM, 0.2), "1", "Quiet Street"),
    _door("q2", *_along(QW, QM, 0.4, -0.0002), "2", "Quiet Street"),
    _door("q3", *_along(QW, QM, 0.6), "3", "Quiet Street"),
    _door("q4", *_along(QW, QM, 0.8, -0.0002), "4", "Quiet Street"),
    _door("q5", *_along(QM, QE, 0.2), "5", "Quiet Street"),
    _door("q6", *_along(QM, QE, 0.4, -0.0002), "6", "Quiet Street"),
    _door("q7", *_along(QM, QE, 0.6), "7", "Quiet Street"),
    _door("q8", *_along(QM, QE, 0.8, -0.0002), "8", "Quiet Street"),
]

BUSY_DOORS = [
    _door("b1", *_along(BW, BE, 0.2), "1", "Busy Road"),
    _door("b2", *_along(BW, BE, 0.3, -0.0002), "2", "Busy Road"),
    _door("b3", *_along(BW, BE, 0.6), "3", "Busy Road"),
    _door("b4", *_along(BW, BE, 0.7, -0.0002), "4", "Busy Road"),
]


@pytest.fixture
def faces() -> list[Blockface]:
    return build_blockfaces(build_stops(QUIET_DOORS + BUSY_DOORS), WAYS)


def _named(faces: list[Blockface], street: str) -> list[Blockface]:
    return [f for f in faces if f.street == street]


class TestParity:
    def test_australian_numbering_puts_evens_on_one_side(self):
        assert parity("2") == EVEN
        assert parity("3") == ODD

    def test_a_number_suffix_does_not_change_the_side(self):
        assert parity("2A") == EVEN
        assert parity("31-37") == ODD

    def test_a_number_without_digits_does_not_invent_a_third_side(self):
        assert parity("Rear") in (EVEN, ODD)


class TestBusyStreets:
    def test_arterial_classes_are_busy(self):
        assert is_busy([BUSY])
        assert is_busy([WalkWay(9, [BW, BE], {"highway": "secondary", "name": "X"})])

    def test_a_quiet_residential_street_is_not_busy(self):
        assert not is_busy([QUIET_WEST])

    def test_four_lanes_make_a_street_busy_whatever_its_class(self):
        wide = WalkWay(9, [BW, BE], {"highway": "residential", "name": "X", "lanes": "4"})
        assert is_busy([wide])

    def test_sixty_kilometres_an_hour_makes_a_street_busy(self):
        fast = WalkWay(9, [BW, BE], {"highway": "residential", "name": "X", "maxspeed": "60"})
        assert is_busy([fast])
        slow = WalkWay(9, [BW, BE], {"highway": "residential", "name": "X", "maxspeed": "50"})
        assert not is_busy([slow])

    def test_one_arterial_segment_makes_the_whole_run_busy(self):
        assert is_busy([QUIET_WEST, BUSY])

    def test_unparseable_speed_and_lane_tags_are_ignored(self):
        odd = WalkWay(9, [BW, BE], {"highway": "residential", "name": "X", "maxspeed": "walk"})
        assert not is_busy([odd])


class TestStreetNetwork:
    def test_a_street_is_split_at_a_cross_street(self):
        network = StreetNetwork(WAYS)
        assert len(network.spans_for_street("Quiet Street")) == 2

    def test_a_t_junction_splits_the_street_it_runs_into(self):
        """Cross Street ends on Busy Road, so Busy Road is two blocks either side."""
        network = StreetNetwork(WAYS)
        assert len(network.spans_for_street("Busy Road")) == 2

    def test_a_street_with_no_junctions_stays_one_span(self):
        cul_de_sac = WalkWay(
            30, [QE, (-37.7990, 145.0550), (-37.7980, 145.0560)],
            {"highway": "residential", "name": "Dead End Court"},
        )
        assert len(StreetNetwork([cul_de_sac]).spans_for_street("Dead End Court")) == 1

    def test_a_span_covers_every_segment_between_its_two_intersections(self):
        network = StreetNetwork(WAYS)
        west = network.span_for("Quiet Street", (-37.7998, 145.0505))
        assert west is not None
        assert west.length_m == pytest.approx(176, abs=5)

    def test_an_unnamed_driveway_does_not_start_a_new_block(self):
        """Kew has a driveway every 20 m; splitting on them would be useless."""
        network = StreetNetwork(WAYS)
        without = StreetNetwork([w for w in WAYS if w is not DRIVEWAY])
        assert len(network.spans_for_street("Quiet Street")) == len(
            without.spans_for_street("Quiet Street")
        )

    def test_an_unwalkable_way_is_not_part_of_any_street(self):
        gated = WalkWay(9, [QE, (-37.799, 145.056)], {"highway": "service", "name": "Gated Way", "access": "private"})
        assert StreetNetwork([*WAYS, gated]).spans_for_street("Gated Way") == []

    def test_a_street_absent_from_the_extract_has_no_spans(self):
        assert StreetNetwork(WAYS).spans_for_street("Nowhere Road") == []

    def test_abbreviated_street_names_resolve_to_the_same_spans(self):
        network = StreetNetwork(WAYS)
        assert network.spans_for_street("Quiet St") == network.spans_for_street("Quiet Street")

    def test_a_point_far_from_the_street_does_not_snap_to_it(self):
        network = StreetNetwork(WAYS)
        assert network.span_for("Quiet Street", (-37.7000, 145.0520)) is None

    def test_two_spans_sharing_a_corner_get_distinct_ids(self):
        """A V-shaped street whose junction sits at the bottom of the V gives
        both spans the same lowest coordinate; without a tiebreak the two
        blocks silently merge into one blockface."""
        vee = WalkWay(40, [VW, VBOTTOM, VE], {"highway": "residential", "name": "Vee Street"})
        cross = WalkWay(41, [VBOTTOM, (-37.8020, 145.0520)], {"highway": "residential", "name": "Cross Street"})
        spans = StreetNetwork([vee, cross]).spans_for_street("Vee Street")
        assert len(spans) == 2
        assert len({s.span_id for s in spans}) == 2

    def test_span_order_does_not_depend_on_way_ordering(self):
        forwards = StreetNetwork(WAYS).spans_for_street("Quiet Street")
        backwards = StreetNetwork(list(reversed(WAYS))).spans_for_street("Quiet Street")
        assert [s.span_id for s in forwards] == [s.span_id for s in backwards]


class TestBlockfaceGrouping:
    def test_a_quiet_street_is_split_only_at_its_intersections(self, faces):
        quiet = _named(faces, "Quiet Street")
        assert len(quiet) == 2
        assert [f.number_range for f in quiet] == [("1", "4"), ("5", "8")]

    def test_both_sides_of_a_quiet_street_share_one_blockface(self, faces):
        quiet = _named(faces, "Quiet Street")
        assert {f.side for f in quiet} == {BOTH}
        assert all(not f.one_side_per_pass for f in quiet)

    def test_a_busy_road_is_split_into_one_blockface_per_side(self, faces):
        busy = _named(faces, "Busy Road")
        assert {f.side for f in busy} == {EVEN, ODD}
        assert all(f.one_side_per_pass for f in busy)

    def test_a_busy_side_holds_only_its_own_parity(self, faces):
        for face in _named(faces, "Busy Road"):
            assert {parity(s.number) for s in face.stops} == {face.side}

    def test_every_stop_lands_in_exactly_one_blockface(self, faces):
        stops = build_stops(QUIET_DOORS + BUSY_DOORS)
        assigned = [s.stop_id for f in faces for s in f.stops]
        assert sorted(assigned) == sorted(s.stop_id for s in stops)

    def test_no_blockface_is_empty(self, faces):
        assert all(f.stop_count > 0 for f in faces)

    def test_blockface_ids_are_unique(self, faces):
        assert len({f.blockface_id for f in faces}) == len(faces)

    def test_stops_within_a_blockface_run_in_house_number_order(self, faces):
        for face in faces:
            numbers = [int(s.number) for s in face.stops]
            assert numbers == sorted(numbers)

    def test_blockfaces_are_ordered_by_street_then_first_number(self, faces):
        keys = [(f.street, int(f.number_range[0])) for f in faces]
        assert keys == sorted(keys)


class TestSameNamedStreets:
    def test_two_streets_of_the_same_name_never_share_a_blockface(self):
        """The Phase 1 carry-over: keying on name alone merges the two Mary Streets."""
        far_west = WalkWay(20, [(-37.79, 145.03), (-37.79, 145.032)], {"highway": "residential", "name": "Mary Street"})
        far_east = WalkWay(21, [(-37.84, 145.10), (-37.84, 145.102)], {"highway": "residential", "name": "Mary Street"})
        doors = [
            _door("w1", -37.7898, 145.0310, "5", "Mary Street"),
            _door("e1", -37.8398, 145.1010, "5", "Mary Street"),
        ]
        faces = build_blockfaces(build_stops(doors), [far_west, far_east])
        assert len(faces) == 2
        assert len({f.blockface_id for f in faces}) == 2

    def test_a_stop_joins_the_nearer_of_two_same_named_streets(self):
        far_west = WalkWay(20, [(-37.79, 145.03), (-37.79, 145.032)], {"highway": "residential", "name": "Mary Street"})
        far_east = WalkWay(21, [(-37.84, 145.10), (-37.84, 145.102)], {"highway": "residential", "name": "Mary Street"})
        doors = [_door("e1", -37.8398, 145.1010, "5", "Mary Street")]
        [face] = build_blockfaces(build_stops(doors), [far_west, far_east])
        assert face.centroid[1] == pytest.approx(145.1010)


class TestOffNetworkFallback:
    def test_a_street_with_no_geometry_still_produces_a_blockface(self):
        """15 of Kew's 774 streets have addresses but no named way in the extract."""
        doors = [
            _door("o1", -37.7000, 145.0500, "1", "Ghost Road"),
            _door("o2", -37.7001, 145.0501, "3", "Ghost Road"),
        ]
        [face] = build_blockfaces(build_stops(doors), WAYS)
        assert face.off_network is True
        assert face.door_count == 2

    def test_a_door_set_well_back_from_its_street_still_matches_it(self):
        """Large estates set doors back a long way down a private drive."""
        set_back = _door("s1", -37.8018, 145.0510, "9", "Quiet Street")
        [face] = [
            f for f in build_blockfaces(build_stops([set_back]), WAYS)
            if f.street == "Quiet Street"
        ]
        assert face.off_network is False

    def test_the_fallback_still_separates_same_named_streets(self):
        doors = [
            _door("o1", -37.7000, 145.0500, "1", "Ghost Road"),
            _door("o2", -37.7500, 145.0900, "1", "Ghost Road"),
        ]
        faces = build_blockfaces(build_stops(doors), WAYS)
        assert len(faces) == 2

    def test_on_network_stops_are_not_marked_off_network(self, faces):
        assert all(not f.off_network for f in faces)

    def test_a_fallback_inherits_the_arterial_status_of_its_own_street(self):
        """Burke Road's carriageway is clipped where its addresses are, but a
        stop stranded there is still on Burke Road and must not be zigzagged."""
        stranded = _door("s1", -37.7500, 145.0900, "1", "Busy Road")
        [face] = [
            f for f in build_blockfaces(build_stops([stranded]), WAYS) if f.off_network
        ]
        assert face.one_side_per_pass is True
        assert face.highway == "primary"

    def test_a_fallback_on_a_quiet_street_stays_zigzag_friendly(self):
        stranded = _door("s1", -37.7500, 145.0900, "1", "Quiet Street")
        [face] = [
            f for f in build_blockfaces(build_stops([stranded]), WAYS) if f.off_network
        ]
        assert face.one_side_per_pass is False
        assert face.side == BOTH

    def test_a_street_missing_from_the_extract_is_assumed_busy(self):
        """Canterbury Road has 81 doors and no geometry; guessing 'quiet'
        would route volunteers across an arterial."""
        doors = [
            _door("g1", -37.7000, 145.0500, "1", "Ghost Road"),
            _door("g2", -37.7001, 145.0501, "2", "Ghost Road"),
        ]
        faces = build_blockfaces(build_stops(doors), WAYS)
        assert all(f.one_side_per_pass for f in faces)
        assert {f.side for f in faces} == {EVEN, ODD}


class TestWorkloadAndSplitting:
    def test_blockface_minutes_are_dwell_plus_walking(self, faces):
        for face in faces:
            assert face.minutes == pytest.approx(face.dwell_minutes + face.walk_minutes)
            assert face.walk_minutes > 0

    def test_door_count_sums_the_doors_behind_every_stop(self):
        doors = QUIET_DOORS + [
            _door(f"u{i}", *_along(QW, QM, 0.2), "1", "Quiet Street", unit=str(i)) for i in range(4)
        ]
        faces = build_blockfaces(build_stops(doors), WAYS)
        assert sum(f.door_count for f in faces) == len(doors)

    def test_an_oversized_run_is_split_into_contiguous_parts(self):
        many = [
            _door(f"m{i}", *_along(QW, QM, 0.05 + i * 0.02), str(i + 1), "Quiet Street")
            for i in range(40)
        ]
        faces = _named(build_blockfaces(build_stops(many), WAYS), "Quiet Street")
        assert len(faces) > 1
        assert all(f.minutes <= MAX_BLOCKFACE_MINUTES * 1.25 for f in faces)
        numbers = [int(s.number) for f in faces for s in f.stops]
        assert numbers == sorted(numbers), "parts must stay contiguous in number order"

    def test_a_run_within_budget_is_left_whole(self, faces):
        assert all("/" not in f.blockface_id for f in faces)

    def test_splitting_loses_no_stops_and_keeps_ids_unique(self):
        many = [
            _door(f"m{i}", *_along(QW, QM, 0.05 + i * 0.02), str(i + 1), "Quiet Street")
            for i in range(40)
        ]
        stops = build_stops(many)
        faces = build_blockfaces(stops, WAYS)
        assert sum(f.stop_count for f in faces) == len(stops)
        assert len({f.blockface_id for f in faces}) == len(faces)


class TestSplitIntoParts:
    def _stops(self, count: int):
        return build_stops(
            [_door(f"s{i}", -37.8, 145.05 + i * 1e-5, str(i + 1), "Quiet Street") for i in range(count)]
        )

    def test_a_short_run_is_one_part(self):
        parts = split_into_parts(self._stops(3), length_m=100, max_minutes=45)
        assert len(parts) == 1

    def test_walking_distance_is_shared_between_the_parts(self):
        parts = split_into_parts(self._stops(40), length_m=900, max_minutes=45)
        assert sum(length for _stops, length in parts) == pytest.approx(900)

    def test_no_part_is_left_empty(self):
        parts = split_into_parts(self._stops(4), length_m=100, max_minutes=1)
        assert all(chunk for chunk, _length in parts)

    def test_parts_cannot_outnumber_the_stops(self):
        parts = split_into_parts(self._stops(3), length_m=10_000, max_minutes=1)
        assert len(parts) <= 3

    def test_every_stop_survives_the_split_exactly_once(self):
        stops = self._stops(37)
        parts = split_into_parts(stops, length_m=500, max_minutes=10)
        rebuilt = [s for chunk, _length in parts for s in chunk]
        assert rebuilt == stops

    def test_parts_are_roughly_balanced(self):
        stops = self._stops(40)
        parts = split_into_parts(stops, length_m=400, max_minutes=20)
        sizes = [len(chunk) for chunk, _length in parts]
        assert max(sizes) - min(sizes) <= 2


class TestSerialisation:
    def test_the_label_reads_like_a_volunteer_instruction(self, faces):
        quiet = _named(faces, "Quiet Street")[0]
        assert quiet.label.startswith("Quiet Street #1-#4 - 4 doors - ")
        assert quiet.label.endswith(" min")

    def test_a_busy_side_is_named_in_the_label(self, faces):
        even = next(f for f in _named(faces, "Busy Road") if f.side == EVEN)
        assert even.label.startswith("Busy Road (even) ")

    def test_a_single_stop_run_is_labelled_with_one_number(self):
        doors = [_door("s1", *_along(BW, BE, 0.2), "1", "Busy Road")]
        [face] = build_blockfaces(build_stops(doors), WAYS)
        assert " #1 - 1 door - " in face.label

    def test_a_range_house_number_is_joined_readably(self):
        """'#2-#30-38' is unreadable; OSM numbers can be ranges themselves."""
        doors = [
            _door("r1", *_along(QW, QM, 0.2), "2", "Quiet Street"),
            _door("r2", *_along(QW, QM, 0.8), "30-38", "Quiet Street"),
        ]
        [face] = build_blockfaces(build_stops(doors), WAYS)
        assert "#2 to #30-38" in face.label

    def test_payload_carries_the_planning_fields(self, faces):
        payload = _named(faces, "Busy Road")[0].to_dict()
        assert payload["one_side_per_pass"] is True
        assert payload["highway"] == "primary"
        assert payload["side"] in (EVEN, ODD)
        # Each field is rounded independently, so the parts sum to the whole
        # only to within their combined rounding error.
        assert payload["minutes"] == pytest.approx(
            payload["dwell_minutes"] + payload["walk_minutes"], abs=0.15
        )

    def test_a_blockface_knows_its_own_geometry(self, faces):
        quiet = _named(faces, "Quiet Street")[0]
        assert quiet.path
        assert all(len(chain) >= 2 for chain in quiet.path)
