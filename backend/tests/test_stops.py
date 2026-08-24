from __future__ import annotations

import pytest

from app.osm.snapshot import Address
from app.stops import (
    APPROACH_SECONDS,
    GATED_COMPLEX_DOOR_THRESHOLD,
    PER_DOOR_SECONDS,
    build_stops,
    dwell_seconds,
    sort_key,
    stop_groups,
)
from tests.conftest import FIXTURE_ADDRESSES


def _block(street: str, number: str, doors: int, lat=-37.805, lon=145.054) -> list[Address]:
    return [
        Address(
            osm_id=f"{street[:2]}{number}-{i}".lower(),
            lat=lat,
            lon=lon,
            number=number,
            street=street,
            unit=str(i + 1),
        )
        for i in range(doors)
    ]


class TestGrouping:
    def test_doors_collapse_into_stops(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        assert len(FIXTURE_ADDRESSES) == 9
        assert len(stops) == 7

    def test_a_multi_unit_block_is_one_stop_carrying_its_doors(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        brenbeal = next(s for s in stops if s.street == "Brenbeal Street")
        assert brenbeal.door_count == 3
        assert brenbeal.units == ("1", "2", "3")
        assert brenbeal.label == "14 Brenbeal Street (3 doors)"

    def test_a_single_door_stop_is_labelled_without_a_door_count(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        assert next(s for s in stops if s.number == "22").label == "22 Yerrin Street"

    def test_stop_position_is_the_centroid_of_its_doors(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        brenbeal = next(s for s in stops if s.street == "Brenbeal Street")
        assert brenbeal.lat == pytest.approx(-37.802033, abs=1e-5)
        assert brenbeal.lon == pytest.approx(145.051066, abs=1e-5)

    def test_same_named_streets_far_apart_stay_separate_stops(self):
        """Two Mary Streets 5.8 km apart must never merge into one stop."""
        addresses = [
            Address(osm_id="m1", lat=-37.792, lon=145.042, number="5", street="Mary Street"),
            Address(osm_id="m2", lat=-37.845, lon=145.098, number="5", street="Mary Street"),
        ]
        stops = build_stops(addresses)
        assert len(stops) == 2
        assert {s.door_count for s in stops} == {1}

    def test_a_genuine_unit_block_is_not_split_by_the_clustering(self):
        stops = build_stops(_block("Kireep Road", "2A", 25))
        assert len(stops) == 1
        assert stops[0].door_count == 25

    def test_stop_groups_and_build_stops_agree(self):
        addresses = list(FIXTURE_ADDRESSES)
        assert len(stop_groups(addresses)) == len(build_stops(addresses))

    def test_every_door_lands_in_exactly_one_stop(self):
        addresses = list(FIXTURE_ADDRESSES) + _block("Kireep Road", "2A", 6)
        stops = build_stops(addresses)
        assigned = [d.osm_id for s in stops for d in s.doors]
        assert sorted(assigned) == sorted(a.osm_id for a in addresses)


class TestStopIdentity:
    def test_ids_are_unique(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        assert len({s.stop_id for s in stops}) == len(stops)

    def test_ids_are_stable_when_the_snapshot_is_reordered(self):
        forwards = build_stops(list(FIXTURE_ADDRESSES))
        backwards = build_stops(list(reversed(FIXTURE_ADDRESSES)))
        assert [s.stop_id for s in forwards] == [s.stop_id for s in backwards]

    def test_stops_are_ordered_by_street_then_house_number(self):
        stops = build_stops(list(FIXTURE_ADDRESSES))
        streets = [s.street for s in stops]
        assert streets == sorted(streets)

    def test_house_numbers_sort_numerically_not_lexically(self):
        assert sorted(["10", "9", "100", "2"], key=sort_key) == ["2", "9", "10", "100"]

    def test_house_number_suffixes_sort_after_the_bare_number(self):
        assert sorted(["2A", "2", "2B"], key=sort_key) == ["2", "2A", "2B"]

    def test_a_number_with_no_digits_does_not_crash_the_sort(self):
        assert sort_key("Rear") == (0, "Rear")


class TestDwell:
    def test_dwell_is_approach_plus_time_per_door(self):
        assert dwell_seconds(1) == APPROACH_SECONDS + PER_DOOR_SECONDS
        assert dwell_seconds(4) == APPROACH_SECONDS + 4 * PER_DOOR_SECONDS

    def test_dwell_grows_with_door_count_up_to_the_gated_threshold(self):
        below = [dwell_seconds(n) for n in range(1, GATED_COMPLEX_DOOR_THRESHOLD + 1)]
        assert below == sorted(below)
        assert len(set(below)) == len(below)

    def test_gated_blocks_are_capped_rather_than_charged_per_door(self):
        """A 95-door tower is one buzzer panel, not 95 walk-ups."""
        capped = dwell_seconds(95)
        assert capped == dwell_seconds(GATED_COMPLEX_DOOR_THRESHOLD)
        assert capped < dwell_seconds(95, cap_doors=None)

    def test_the_cap_is_continuous_so_no_stop_gains_by_losing_a_door(self):
        threshold = GATED_COMPLEX_DOOR_THRESHOLD
        assert dwell_seconds(threshold) - dwell_seconds(threshold - 1) == PER_DOOR_SECONDS
        assert dwell_seconds(threshold + 1) == dwell_seconds(threshold)

    def test_a_stop_reports_both_the_capped_and_the_honest_dwell(self):
        [stop] = build_stops(_block("Cotham Road", "378", 95))
        assert stop.is_gated_candidate
        assert stop.dwell_minutes == pytest.approx(10.5)
        assert stop.uncapped_dwell_seconds / 60 == pytest.approx(119.25)

    def test_ordinary_stops_are_not_flagged_as_gated(self):
        [stop] = build_stops(_block("Yerrin Street", "22", 3))
        assert not stop.is_gated_candidate
        assert stop.dwell_seconds == stop.uncapped_dwell_seconds


class TestSerialisation:
    def test_payload_carries_the_planning_fields(self):
        [stop] = build_stops(_block("Yerrin Street", "22", 2))
        payload = stop.to_dict()
        assert payload["door_count"] == 2
        assert payload["dwell_minutes"] == 3.0
        assert "gated_candidate" not in payload
        assert "uncapped_dwell_minutes" not in payload

    def test_payload_does_not_repeat_the_geojson_geometry(self):
        """24,366 stops x a duplicated lat/lon is megabytes over a phone link."""
        payload = build_stops(_block("Yerrin Street", "22", 2))[0].to_dict()
        assert not {"id", "lat", "lon"} & payload.keys()

    def test_gated_payloads_expose_the_uncapped_figure(self):
        [stop] = build_stops(_block("Cotham Road", "378", 95))
        payload = stop.to_dict()
        assert payload["gated_candidate"] is True
        assert payload["uncapped_dwell_minutes"] == pytest.approx(119.2, abs=0.1)
