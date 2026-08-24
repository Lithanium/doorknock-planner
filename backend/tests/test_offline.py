from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

pytestmark = pytest.mark.snapshot


@pytest.fixture
def offline_client(monkeypatch):
    """A client that cannot reach the network, over the real cached district.

    This is the test for the central design claim: after the one-time extract,
    planning must never touch Overpass, Nominatim or any other service. Name
    resolution and outbound connections raise, so any request path that reaches
    for the network fails the test.

    Two things are deliberately left working, since blocking them would fail
    for reasons unrelated to the app: socket *creation* (asyncio builds its own
    self-pipe with ``socket.socketpair()``) and ``httpx``'s ASGI transport
    (``TestClient`` is itself an ``httpx.Client``). Only the transports that
    actually leave this machine are blocked.
    """
    path = load_settings().snapshot_path
    if not path.exists():
        pytest.skip(f"no cached district snapshot at {path}; run `make fetch-district`")

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted while serving a request")

    import httpx

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)
    return TestClient(create_app(snapshot_path=path))


def test_health_works_offline(offline_client):
    body = offline_client.get("/api/health").json()
    assert body["snapshot_available"] is True
    assert "Kew" in body["district"]


def test_district_boundary_works_offline(offline_client):
    body = offline_client.get("/api/district").json()
    assert body["boundary"]["type"] == "MultiPolygon"
    assert body["doors"] > 25_000


def test_all_addresses_serve_offline(offline_client):
    body = offline_client.get("/api/addresses").json()
    assert body["count"] > 25_000
    assert body["truncated"] is False


def test_all_stops_serve_offline(offline_client):
    body = offline_client.get("/api/stops").json()
    assert body["count"] > 20_000
    assert body["doors"] > body["count"]


def test_blockfaces_serve_offline(offline_client):
    body = offline_client.get("/api/blockfaces", params={"limit": 20_000}).json()
    assert body["count"] > 1_000
    assert body["truncated"] is False


def test_coverage_report_works_offline(offline_client):
    body = offline_client.get("/api/coverage").json()
    assert body["addresses_missing_street"] == 0
    assert body["effort"]["doors_per_pair_session"] > 0


def test_geocoding_works_offline(offline_client):
    body = offline_client.get("/api/geocode", params={"q": "Cotham Road"}).json()
    assert body["candidates"]
    assert body["candidates"][0]["inside_district"] is True


def test_hub_preview_works_offline(offline_client):
    body = offline_client.get(
        "/api/hub/preview", params={"lat": -37.8060, "lon": 145.0300, "radius_m": 800}
    ).json()
    assert body["doors_within"] > 0
    assert body["nearest_address"]
