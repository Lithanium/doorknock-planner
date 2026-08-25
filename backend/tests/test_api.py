from __future__ import annotations

import pytest


def test_health_without_a_snapshot_is_still_ok(empty_client):
    body = empty_client.get("/api/health").json()
    assert body == {"status": "ok", "snapshot_available": False}


def test_health_reports_the_cached_district(client):
    body = client.get("/api/health").json()
    assert body["snapshot_available"] is True
    assert body["district"] == "Electoral district of Testville"
    assert body["doors"] == 9


@pytest.mark.parametrize(
    "path",
    [
        "/api/district",
        "/api/addresses",
        "/api/stops",
        "/api/blockfaces",
        "/api/coverage",
        "/api/geocode?q=x",
        "/api/reverse?lat=0&lon=0",
    ],
)
def test_endpoints_explain_how_to_fetch_the_snapshot_when_it_is_missing(empty_client, path):
    response = empty_client.get(path)
    assert response.status_code == 503
    assert "fetch-district" in response.json()["detail"]


def test_district_returns_a_drawable_boundary(client):
    body = client.get("/api/district").json()
    assert body["name"] == "Electoral district of Testville"
    assert body["boundary"]["type"] == "MultiPolygon"
    lon, lat = body["boundary"]["coordinates"][0][0][0]
    assert 145.0 < lon < 145.1 and -37.9 < lat < -37.7
    assert body["bbox"] == pytest.approx([-37.81, 145.04, -37.79, 145.06])
    assert body["doors"] == 9
    assert body["walkable_ways"] == 3


def test_addresses_returns_geojson_in_lon_lat_order(client):
    body = client.get("/api/addresses").json()
    assert body["type"] == "FeatureCollection"
    assert body["count"] == 9
    assert body["truncated"] is False
    feature = body["features"][0]
    assert feature["geometry"]["coordinates"] == [145.050, -37.800]
    assert feature["properties"]["label"] == "22 Yerrin Street"


def test_addresses_can_be_filtered_to_a_bbox(client):
    body = client.get("/api/addresses", params={"bbox": "-37.81,145.04,-37.79,145.06"}).json()
    assert body["count"] == 8
    assert all("Outside" not in f["properties"]["street"] for f in body["features"])


def test_addresses_reports_truncation(client):
    body = client.get("/api/addresses", params={"limit": 3}).json()
    assert body["count"] == 3
    assert body["truncated"] is True


def test_addresses_rejects_a_malformed_bbox(client):
    response = client.get("/api/addresses", params={"bbox": "not-a-bbox"})
    assert response.status_code == 400


def test_stops_collapse_the_address_list(client):
    body = client.get("/api/stops").json()
    assert body["type"] == "FeatureCollection"
    assert body["count"] == 7
    assert body["doors"] == 9
    assert body["truncated"] is False


def test_stops_carry_door_counts_and_dwell(client):
    body = client.get("/api/stops").json()
    brenbeal = next(
        f for f in body["features"] if f["properties"]["street"] == "Brenbeal Street"
    )
    assert brenbeal["properties"]["door_count"] == 3
    assert brenbeal["properties"]["dwell_minutes"] > 0
    assert "gated_candidate" not in brenbeal["properties"]
    assert brenbeal["geometry"]["coordinates"][0] == pytest.approx(145.0510, abs=1e-3)


def test_stops_can_be_filtered_to_a_bbox(client):
    body = client.get("/api/stops", params={"bbox": "-37.81,145.04,-37.79,145.06"}).json()
    assert body["count"] == 6
    assert all("Outside" not in f["properties"]["street"] for f in body["features"])


def test_stops_reject_a_malformed_bbox(client):
    assert client.get("/api/stops", params={"bbox": "1,2"}).status_code == 400


def test_stops_report_truncation(client):
    body = client.get("/api/stops", params={"limit": 2}).json()
    assert body["count"] == 2
    assert body["truncated"] is True


def test_blockfaces_return_drawable_runs_of_work(client):
    body = client.get("/api/blockfaces").json()
    assert body["type"] == "FeatureCollection"
    assert body["count"] > 0
    assert body["doors"] == 9
    assert body["minutes"] > 0
    feature = body["features"][0]
    assert feature["geometry"]["type"] == "MultiLineString"
    assert feature["properties"]["stops"] >= 1
    assert feature["properties"]["side"] in ("even", "odd", "both")
    assert feature["properties"]["stop_ids"]


def test_blockfaces_can_be_filtered_to_one_street(client):
    body = client.get("/api/blockfaces", params={"street": "Yerrin St"}).json()
    assert body["count"] >= 1
    assert {f["properties"]["street"] for f in body["features"]} == {"Yerrin Street"}


def test_blockfaces_can_be_filtered_to_a_bbox(client):
    body = client.get("/api/blockfaces", params={"bbox": "-37.81,145.04,-37.79,145.06"}).json()
    assert 0 < body["count"] <= client.get("/api/blockfaces").json()["count"]


def test_blockfaces_account_for_every_stop_exactly_once(client):
    stop_ids = {f["id"] for f in client.get("/api/stops").json()["features"]}
    assigned = [
        stop_id
        for f in client.get("/api/blockfaces").json()["features"]
        for stop_id in f["properties"]["stop_ids"]
    ]
    assert sorted(assigned) == sorted(stop_ids)


def test_coverage_includes_the_effort_estimate(client):
    body = client.get("/api/coverage").json()
    assert body["doors"] == 9
    assert body["stops"] == 7
    assert body["multi_unit_stops"] == 1
    assert body["cluster_histogram"] == {"1": 6, "3": 1}
    assert body["effort"]["doors_per_pair_session"] > 0


def test_coverage_reports_blockfaces_and_knock_hours(client):
    body = client.get("/api/coverage").json()
    assert body["blockfaces"] == client.get("/api/blockfaces").json()["count"]
    assert body["knock_hours"] > 0
    assert body["uncapped_knock_hours"] >= body["knock_hours"]


def test_geocode_returns_candidates_with_a_district_containment_flag(client):
    body = client.get("/api/geocode", params={"q": "22 Yerrin St"}).json()
    [candidate] = body["candidates"]
    assert candidate["match_type"] == "exact"
    assert candidate["inside_district"] is True


def test_geocode_flags_a_candidate_outside_the_district(client):
    body = client.get("/api/geocode", params={"q": "5 Outside Avenue"}).json()
    assert body["candidates"][0]["inside_district"] is False


def test_geocode_returns_an_empty_list_for_an_unknown_street(client):
    body = client.get("/api/geocode", params={"q": "9 Nonexistent Boulevard"}).json()
    assert body["candidates"] == []


def test_geocode_requires_a_query(client):
    assert client.get("/api/geocode", params={"q": ""}).status_code == 422


def test_reverse_returns_the_nearest_address_and_its_distance(client):
    body = client.get("/api/reverse", params={"lat": -37.8001, "lon": 145.0500}).json()
    assert body["label"] == "22 Yerrin Street"
    assert body["distance_m"] < 20
    assert body["inside_district"] is True


def test_hub_preview_summarises_the_reachable_workload(client):
    body = client.get(
        "/api/hub/preview", params={"lat": -37.800, "lon": 145.050, "radius_m": 800}
    ).json()
    assert body["inside_district"] is True
    assert body["doors_within"] == 8
    assert body["stops_within"] == 6
    assert body["streets_within"] == 4
    assert body["nearest_address"] == "22 Yerrin Street"
    assert body["effort"]["doors_per_pair_session"] > 0


def test_hub_preview_radius_limits_the_workload(client):
    small = client.get(
        "/api/hub/preview", params={"lat": -37.800, "lon": 145.050, "radius_m": 100}
    ).json()
    assert small["doors_within"] < 8


def test_hub_preview_rejects_an_absurd_radius(client):
    assert client.get(
        "/api/hub/preview", params={"lat": -37.8, "lon": 145.05, "radius_m": 99_999}
    ).status_code == 422


def test_hub_preview_reports_walking_reachability(client):
    body = client.get(
        "/api/hub/preview", params={"lat": -37.800, "lon": 145.050, "radius_m": 800}
    ).json()
    walk = body["walk"]
    # Only the Yerrin Street doors sit on the connected fixture way; the
    # others are near disconnected or absent ways, so crow-flies overcounts.
    assert 0 < walk["doors_within"] <= body["doors_within"]
    assert walk["stops_within"] <= body["stops_within"]
    assert walk["minutes_to_farthest"] >= 0


def test_hub_preview_reports_blockfaces_and_knocking_hours(client):
    body = client.get(
        "/api/hub/preview", params={"lat": -37.800, "lon": 145.050, "radius_m": 800}
    ).json()
    walk = body["walk"]
    assert walk["blockfaces_within"] > 0
    assert walk["knock_hours"] > 0


def test_walk_route_returns_geometry_and_minutes(client):
    body = client.get(
        "/api/walk/route",
        params={"from_lat": -37.800, "from_lon": 145.050, "to_lat": -37.801, "to_lon": 145.0505},
    ).json()
    assert body["geometry"]["type"] == "LineString"
    assert body["geometry"]["coordinates"][0] == [145.050, -37.800]
    assert body["distance_m"] > 0
    assert body["minutes"] == pytest.approx(body["distance_m"] / 80, abs=0.1)
    assert body["detour_ratio"] >= 1.0


def test_walk_route_404s_when_no_path_exists(client):
    # Way 1 (Yerrin Street) and way 3 (the footway) are disconnected in the
    # fixture, so a route between them must fail loudly, not guess.
    response = client.get(
        "/api/walk/route",
        params={"from_lat": -37.800, "from_lon": 145.050, "to_lat": -37.8022, "to_lon": 145.0512},
    )
    assert response.status_code == 404


def test_walk_route_404s_far_from_the_network(client):
    response = client.get(
        "/api/walk/route",
        params={"from_lat": -37.700, "from_lon": 145.200, "to_lat": -37.800, "to_lon": 145.050},
    )
    assert response.status_code == 404


def test_walk_route_without_a_snapshot_explains_the_fix(empty_client):
    response = empty_client.get(
        "/api/walk/route",
        params={"from_lat": -37.8, "from_lon": 145.05, "to_lat": -37.801, "to_lon": 145.051},
    )
    assert response.status_code == 503
    assert "fetch-district" in response.json()["detail"]


def test_territories_split_the_walkable_blockfaces_between_teams(client):
    body = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 2, "radius_m": 800},
    ).json()
    assert body["type"] == "FeatureCollection"
    assert body["team_count"] == 2
    assert body["blockface_count"] == len(body["features"]) > 0
    assert {t["team"] for t in body["teams"]} == {1, 2}
    assert body["target_minutes"] == pytest.approx(body["total_minutes"] / 2, abs=0.1)


def test_territories_assign_every_blockface_to_exactly_one_team(client):
    body = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 3, "radius_m": 800},
    ).json()
    ids = [f["id"] for f in body["features"]]
    assert len(ids) == len(set(ids))
    assert all(1 <= f["properties"]["team"] <= 3 for f in body["features"])


def test_territories_features_carry_workload_properties(client):
    body = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 1, "radius_m": 800},
    ).json()
    for feature in body["features"]:
        props = feature["properties"]
        assert props["street"]
        assert props["minutes"] > 0
        assert props["doors"] > 0
        assert feature["geometry"]["type"] in ("MultiLineString", "MultiPoint")


def test_territories_reject_more_than_the_supported_team_count(client):
    response = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 9, "radius_m": 800},
    )
    assert response.status_code == 422


def test_territories_reject_zero_teams(client):
    response = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 0, "radius_m": 800},
    )
    assert response.status_code == 422


def test_territories_never_reach_beyond_the_radius(client):
    """A blockface that only touches the radius must be trimmed to the part
    inside it, not walked end to end."""
    tight = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 1, "radius_m": 100},
    ).json()
    wide = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 1, "radius_m": 800},
    ).json()
    assert tight["total_minutes"] < wide["total_minutes"]
    assert sum(f["properties"]["doors"] for f in tight["features"]) < sum(
        f["properties"]["doors"] for f in wide["features"]
    )


def test_territories_cover_at_least_the_walkable_workload(client):
    """The two panels scope differently on purpose: `hub/preview` reports what
    is reachable within a real *walk*, while `territories` plans everything in
    the crow-flies circle the map draws, so no street inside it is left out.
    Walking a given distance can never beat the straight line, so territories
    must be a superset - never fewer blockfaces than the walk reports."""
    preview = client.get(
        "/api/hub/preview", params={"lat": -37.800, "lon": 145.050, "radius_m": 800}
    ).json()
    body = client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 2, "radius_m": 800},
    ).json()
    assert body["blockface_count"] >= preview["walk"]["blockfaces_within"] > 0


def test_territories_without_a_snapshot_explain_the_fix(empty_client):
    response = empty_client.get(
        "/api/territories",
        params={"lat": -37.800, "lon": 145.050, "teams": 2},
    )
    assert response.status_code == 503
    assert "fetch-district" in response.json()["detail"]
