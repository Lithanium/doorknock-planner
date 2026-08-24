from __future__ import annotations

from app.coverage import build_coverage_report, estimate_effort


def test_report_counts_doors_and_stops_separately(snapshot):
    report = build_coverage_report(snapshot)
    assert report.doors == 9
    assert report.stops == 7
    assert report.streets == 5


def test_report_identifies_multi_unit_stops(snapshot):
    report = build_coverage_report(snapshot)
    assert report.doors_with_unit == 3
    assert report.multi_unit_stops == 1
    assert report.cluster_histogram == {1: 6, 3: 1}
    assert report.largest_stops[0] == {"street": "Brenbeal Street", "number": "14", "doors": 3}


def test_report_flags_probable_gated_blocks(snapshot):
    from app.osm.snapshot import Address

    snapshot.addresses += [
        Address(osm_id=f"x{i}", lat=-37.805, lon=145.054, number="2A", street="Kireep Road", unit=str(i))
        for i in range(10)
    ]
    report = build_coverage_report(snapshot)
    assert report.gated_complex_candidates == 1


def test_report_summarises_the_walkable_network(snapshot):
    report = build_coverage_report(snapshot)
    assert report.walkable_ways == {"residential": 1, "primary": 1, "footway": 1}


def test_report_describes_the_boundary(snapshot):
    report = build_coverage_report(snapshot)
    assert report.boundary_rings == 1
    assert report.boundary_closed_rings == 1
    assert report.extent_km[0] > 0 and report.extent_km[1] > 0


def test_report_serialises_with_string_histogram_keys(snapshot):
    payload = build_coverage_report(snapshot).to_dict()
    assert payload["cluster_histogram"] == {"1": 6, "3": 1}
    assert payload["district_name"] == "Electoral district of Testville"


def test_top_streets_are_ranked_by_door_count(snapshot):
    report = build_coverage_report(snapshot)
    assert report.top_streets[0]["street"] in {"Yerrin Street", "Brenbeal Street"}
    assert report.top_streets[0]["doors"] == 3


def test_effort_estimate_is_arithmetically_sound():
    effort = estimate_effort(doors=28_137, session_minutes=180, seconds_per_door=75)
    assert effort["doors_per_pair_session"] == 93
    assert effort["pair_sessions_for_full_coverage"] == 302.5


def test_effort_estimate_scales_with_dwell_time():
    fast = estimate_effort(1000, seconds_per_door=45)
    slow = estimate_effort(1000, seconds_per_door=120)
    assert fast["doors_per_pair_session"] > slow["doors_per_pair_session"]
    assert fast["pair_sessions_for_full_coverage"] < slow["pair_sessions_for_full_coverage"]
