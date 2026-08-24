from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.coverage import estimate_effort, stop_groups
from app.osm.boundary import haversine_m, rings_to_geojson
from app.services import SnapshotMissingError, SnapshotStore
from app.walkgraph import WALKING_SPEED_M_PER_MIN

router = APIRouter(prefix="/api")


def _store(request: Request) -> SnapshotStore:
    return request.app.state.store


def _require_snapshot(request: Request):
    store = _store(request)
    try:
        return store.snapshot
    except SnapshotMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health")
def health(request: Request) -> dict:
    store = _store(request)
    payload: dict = {"status": "ok", "snapshot_available": store.available}
    if store.available:
        snapshot = store.snapshot
        payload |= {
            "district": snapshot.district_name,
            "fetched_at": snapshot.fetched_at,
            "doors": len(snapshot.addresses),
        }
    return payload


@router.get("/district")
def district(request: Request) -> dict:
    snapshot = _require_snapshot(request)
    south, west, north, east = snapshot.bbox
    return {
        "name": snapshot.district_name,
        "relation_id": snapshot.relation_id,
        "fetched_at": snapshot.fetched_at,
        "boundary_source": snapshot.boundary_source,
        "bbox": [south, west, north, east],
        "boundary": rings_to_geojson(snapshot.rings),
        "doors": len(snapshot.addresses),
        "walkable_ways": len(snapshot.ways),
    }


@router.get("/addresses")
def addresses(
    request: Request,
    bbox: str | None = Query(default=None, description="south,west,north,east"),
    limit: int = Query(default=40_000, ge=1, le=100_000),
) -> dict:
    snapshot = _require_snapshot(request)
    selected = snapshot.addresses
    if bbox:
        try:
            south, west, north, east = (float(v) for v in bbox.split(","))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="bbox must be south,west,north,east") from exc
        selected = [
            a for a in selected if south <= a.lat <= north and west <= a.lon <= east
        ]
    truncated = len(selected) > limit
    selected = selected[:limit]
    return {
        "type": "FeatureCollection",
        "truncated": truncated,
        "count": len(selected),
        "features": [
            {
                "type": "Feature",
                "id": a.osm_id,
                "geometry": {"type": "Point", "coordinates": [a.lon, a.lat]},
                "properties": {"label": a.label, "street": a.street, "number": a.number},
            }
            for a in selected
        ],
    }


@router.get("/coverage")
def coverage(request: Request) -> dict:
    snapshot = _require_snapshot(request)
    report = _store(request).coverage
    return {**report.to_dict(), "effort": estimate_effort(len(snapshot.addresses))}


@router.get("/geocode")
def geocode(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=8, ge=1, le=25),
) -> dict:
    snapshot = _require_snapshot(request)
    candidates = _store(request).geocoder.search(q, limit=limit)
    return {
        "query": q,
        "candidates": [
            {**c.to_dict(), "inside_district": snapshot.contains((c.lat, c.lon))}
            for c in candidates
        ],
    }


@router.get("/reverse")
def reverse(request: Request, lat: float, lon: float) -> dict:
    snapshot = _require_snapshot(request)
    nearest = _store(request).geocoder.nearest(lat, lon)
    if nearest is None:
        raise HTTPException(status_code=404, detail="no addresses in snapshot")
    return {
        "label": nearest.label,
        "lat": nearest.lat,
        "lon": nearest.lon,
        "distance_m": round(haversine_m((lat, lon), nearest.point), 1),
        "inside_district": snapshot.contains((lat, lon)),
    }


@router.get("/hub/preview")
def hub_preview(
    request: Request,
    lat: float,
    lon: float,
    radius_m: float = Query(default=800, ge=50, le=3000),
) -> dict:
    """Summarises the workload reachable from a candidate pamphlet hub."""
    snapshot = _require_snapshot(request)
    store = _store(request)
    within = [a for a in snapshot.addresses if haversine_m((lat, lon), a.point) <= radius_m]
    stops = stop_groups(within)
    nearest = store.geocoder.nearest(lat, lon)

    walk_m = store.walk_graph.distances_from((lat, lon), store.address_snaps, radius_m)
    walkable = [a for a in snapshot.addresses if a.osm_id in walk_m]
    return {
        "lat": lat,
        "lon": lon,
        "radius_m": radius_m,
        "inside_district": snapshot.contains((lat, lon)),
        "doors_within": len(within),
        "stops_within": len(stops),
        "streets_within": len({a.street for a in within}),
        "nearest_address": nearest.label if nearest else None,
        "effort": estimate_effort(len(within)),
        "walk": {
            "doors_within": len(walkable),
            "stops_within": len(stop_groups(walkable)),
            "streets_within": len({a.street for a in walkable}),
            "minutes_to_farthest": round(max(walk_m.values()) / WALKING_SPEED_M_PER_MIN, 1)
            if walk_m
            else 0.0,
        },
    }


@router.get("/walk/route")
def walk_route(
    request: Request,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
) -> dict:
    """Shortest walking path between two points, door to door."""
    store = _store(request)
    _require_snapshot(request)
    route = store.walk_graph.route((from_lat, from_lon), (to_lat, to_lon))
    if route is None:
        raise HTTPException(
            status_code=404,
            detail="no walking route found; both points must be near a walkable way",
        )
    crow_flies = haversine_m((from_lat, from_lon), (to_lat, to_lon))
    return {
        "distance_m": round(route.distance_m, 1),
        "minutes": round(route.minutes, 1),
        "crow_flies_m": round(crow_flies, 1),
        "detour_ratio": round(route.distance_m / crow_flies, 2) if crow_flies > 1 else 1.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lat, lon in route.points],
        },
    }
