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
    ["/api/district", "/api/addresses", "/api/coverage", "/api/geocode?q=x", "/api/reverse?lat=0&lon=0"],
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


def test_coverage_includes_the_effort_estimate(client):
    body = client.get("/api/coverage").json()
    assert body["doors"] == 9
    assert body["stops"] == 7
    assert body["multi_unit_stops"] == 1
    assert body["cluster_histogram"] == {"1": 6, "3": 1}
    assert body["effort"]["doors_per_pair_session"] > 0


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
