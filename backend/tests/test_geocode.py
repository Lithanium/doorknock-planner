from __future__ import annotations

import pytest

from app.geocode import LocalGeocoder, normalise_number, normalise_street, parse_query, suffix_variants
from tests.conftest import FIXTURE_ADDRESSES


@pytest.fixture
def geocoder() -> LocalGeocoder:
    return LocalGeocoder(list(FIXTURE_ADDRESSES))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yerrin Street", "yerrin street"),
        ("Yerrin St", "yerrin street"),
        ("YERRIN ST.", "yerrin street"),
        ("Mountain View Rd", "mountain view road"),
        ("High Street South", "high street south"),
        ("Brenbeal St, VIC 3103", "brenbeal street"),
        ("Buchanan Ave", "buchanan avenue"),
        ("Kireep Rd", "kireep road"),
    ],
)
def test_normalise_street_expands_abbreviations_and_strips_noise(raw, expected):
    assert normalise_street(raw) == expected


def test_normalise_number_is_case_and_punctuation_insensitive():
    assert normalise_number("247B") == "247b"
    assert normalise_number("12 ") == "12"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("22 Yerrin Street", (None, "22", "Yerrin Street")),
        ("5/14 Brenbeal Street", ("5", "14", "Brenbeal Street")),
        ("Unit 5, 14 Brenbeal St", ("5", "14", "Brenbeal St")),
        ("14 Brenbeal Street, Balwyn VIC 3103", (None, "14", "Brenbeal Street")),
        ("Yerrin Street", (None, None, "Yerrin Street")),
        ("247B Belmore Road", (None, "247B", "Belmore Road")),
        ("2-4 High Street", (None, "2-4", "High Street")),
        ("31-37 Harp Road", (None, "31-37", "Harp Road")),
    ],
)
def test_parse_query(query, expected):
    assert parse_query(query) == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [("22", ["22"]), ("247B", ["247b"]), ("31-37", ["3137", "31"])],
)
def test_number_keys_cover_ranges(number, expected):
    from app.geocode import number_keys

    assert number_keys(number) == expected


def test_ranged_house_number_matches_either_form():
    """OSM stores frontages like '31-37 Harp Road' as a single record."""
    from app.osm.snapshot import Address

    ranged = LocalGeocoder(
        [Address(osm_id="n1", lat=-37.80, lon=145.03, number="31-37", street="Harp Road")]
    )
    for query in ("31-37 Harp Road", "31 Harp Road", "31-37 Harp Rd"):
        candidates = ranged.search(query)
        assert candidates, query
        assert candidates[0].match_type == "exact", query
        assert candidates[0].number == "31-37"


def test_nearby_numbers_use_the_leading_part_of_a_range():
    from app.osm.snapshot import Address

    geocoder = LocalGeocoder(
        [
            Address(osm_id="n1", lat=-37.80, lon=145.03, number="31-37", street="Harp Road"),
            Address(osm_id="n2", lat=-37.81, lon=145.03, number="900", street="Harp Road"),
        ]
    )
    candidates = geocoder.search("33 Harp Road")
    assert candidates[0].number == "31-37"


def test_suffix_variants_trims_trailing_suburb_tokens():
    assert suffix_variants("Yerrin Street Balwyn North") == [
        "yerrin street balwyn north",
        "yerrin street balwyn",
        "yerrin street",
        "yerrin",
    ]


def test_exact_match_returns_the_address():
    [candidate] = LocalGeocoder(list(FIXTURE_ADDRESSES)).search("22 Yerrin Street")
    assert candidate.match_type == "exact"
    assert candidate.number == "22"
    assert candidate.door_count == 1
    assert candidate.lat == pytest.approx(-37.800)


def test_abbreviated_street_type_still_matches_exactly(geocoder):
    [candidate] = geocoder.search("22 Yerrin St")
    assert candidate.match_type == "exact"
    assert candidate.lat == pytest.approx(-37.800)


def test_suburb_name_ordering_cannot_change_the_result(geocoder):
    """Regression guard for the 'North Balwyn' vs 'Balwyn North' 1.6 km drift.

    A remote geocoder resolves these two orderings to different points. Matching
    against the district's own addresses makes that impossible.
    """
    a = geocoder.search("22 Yerrin St North Balwyn")
    b = geocoder.search("22 Yerrin Street Balwyn North")
    c = geocoder.search("22 Yerrin Street")
    assert a and b and c
    assert (a[0].lat, a[0].lon) == (b[0].lat, b[0].lon) == (c[0].lat, c[0].lon)
    assert a[0].match_type == "exact"


def test_trailing_state_and_postcode_are_ignored(geocoder):
    [candidate] = geocoder.search("22 Yerrin Street, Balwyn North VIC 3104")
    assert candidate.match_type == "exact"
    assert candidate.lat == pytest.approx(-37.800)


def test_multi_unit_stop_collapses_to_one_candidate_with_a_door_count(geocoder):
    [candidate] = geocoder.search("14 Brenbeal Street")
    assert candidate.door_count == 3
    assert "3 doors" in candidate.label
    assert candidate.lat == pytest.approx(-37.80203, abs=1e-4)


def test_unit_prefix_resolves_to_the_same_stop(geocoder):
    with_unit = geocoder.search("2/14 Brenbeal Street")
    without = geocoder.search("14 Brenbeal Street")
    assert (with_unit[0].lat, with_unit[0].lon) == (without[0].lat, without[0].lon)


def test_unknown_house_number_falls_back_to_nearest_numbers(geocoder):
    candidates = geocoder.search("23 Yerrin Street")
    assert candidates
    assert all(c.match_type == "approximate" for c in candidates)
    assert candidates[0].number in {"22", "24"}


def test_street_only_query_returns_a_street_centroid(geocoder):
    [candidate] = geocoder.search("Yerrin Street")
    assert candidate.match_type == "street"
    assert candidate.number is None
    assert candidate.door_count == 3


def test_misspelled_street_is_matched_fuzzily(geocoder):
    candidates = geocoder.search("22 Yerin Street")
    assert candidates
    assert candidates[0].street == "Yerrin Street"
    assert candidates[0].match_type == "fuzzy"


def test_unknown_street_returns_nothing(geocoder):
    assert geocoder.search("12 Nonexistent Boulevard") == []


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_returns_nothing(geocoder, query):
    assert geocoder.search(query) == []


def test_nearest_finds_the_closest_address(geocoder):
    nearest = geocoder.nearest(-37.8001, 145.0500)
    assert nearest is not None
    assert nearest.label == "22 Yerrin Street"


def test_nearest_on_empty_index_returns_none():
    assert LocalGeocoder([]).nearest(0.0, 0.0) is None


def test_street_count(geocoder):
    assert geocoder.street_count == 5


def test_two_streets_of_the_same_name_are_returned_as_separate_candidates():
    """The district really does contain two Mary Streets 5.8 km apart."""
    from app.osm.snapshot import Address

    geocoder = LocalGeocoder(
        [
            Address(osm_id="n1", lat=-37.8060, lon=145.0300, number="15", street="Mary Street"),
            Address(osm_id="n2", lat=-37.7900, lon=145.0850, number="15", street="Mary Street"),
        ]
    )
    candidates = geocoder.search("15 Mary Street")
    assert len(candidates) == 2
    assert {round(c.lat, 4) for c in candidates} == {-37.8060, -37.7900}
    assert all(c.door_count == 1 for c in candidates)
    assert all(" - " in c.label for c in candidates), "candidates must be distinguishable"


def test_a_genuine_unit_block_is_not_split_by_the_clustering():
    from app.osm.snapshot import Address

    block = [
        Address(
            osm_id=f"n{i}",
            lat=-37.8060 + i * 0.0002,
            lon=145.0300,
            number="2A",
            street="Kireep Road",
            unit=str(i),
        )
        for i in range(6)
    ]
    [candidate] = LocalGeocoder(block).search("2A Kireep Road")
    assert candidate.door_count == 6


def test_street_only_query_splits_duplicate_street_names():
    from app.osm.snapshot import Address

    geocoder = LocalGeocoder(
        [
            Address(osm_id="n1", lat=-37.8060, lon=145.0300, number="1", street="Henry Street"),
            Address(osm_id="n2", lat=-37.8062, lon=145.0302, number="3", street="Henry Street"),
            Address(osm_id="n3", lat=-37.7900, lon=145.0850, number="1", street="Henry Street"),
        ]
    )
    candidates = geocoder.search("Henry Street")
    assert len(candidates) == 2
    assert [c.door_count for c in candidates] == [2, 1]


def test_a_long_continuous_street_stays_a_single_cluster():
    """Balwyn Road runs 4.7 km, but its houses chain together at ~20 m spacing."""
    from app.osm.snapshot import Address

    long_street = [
        Address(osm_id=f"n{i}", lat=-37.83 + i * 0.0002, lon=145.08, number=str(i + 1), street="Balwyn Road")
        for i in range(200)
    ]
    [candidate] = LocalGeocoder(long_street).search("Balwyn Road")
    assert candidate.door_count == 200


def test_ambiguous_candidates_are_labelled_with_a_postcode_when_available():
    from app.osm.snapshot import Address

    geocoder = LocalGeocoder(
        [
            Address(
                osm_id="n1", lat=-37.8060, lon=145.0300, number="15", street="Mary Street",
                postcode="3101",
            ),
            Address(
                osm_id="n2", lat=-37.7900, lon=145.0850, number="15", street="Mary Street",
                postcode="3104",
            ),
        ]
    )
    labels = {c.label for c in geocoder.search("15 Mary Street")}
    assert labels == {"15 Mary Street - 3101", "15 Mary Street - 3104"}


def test_label_includes_unit_when_present():
    unit_address = next(a for a in FIXTURE_ADDRESSES if a.unit)
    assert unit_address.label == "1/14 Brenbeal Street"
