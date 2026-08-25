from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.blockface import Blockface
from app.coverage import estimate_effort
from app.geocode import normalise_street
from app.osm.boundary import haversine_m, rings_to_geojson
from app.services import SnapshotMissingError, SnapshotStore
from app.stops import build_stops
from app.territory import MAX_TEAMS, build_territories
from app.walkgraph import WALKING_SPEED_M_PER_MIN

router = APIRouter(prefix="/api")


def _store(request: Request) -> SnapshotStore:
    return request.app.state.store


def _blockfaces_within(store: SnapshotStore, reachable_ids: set[str]) -> list[Blockface]:
    """The session's blockfaces, each trimmed to the stops inside the radius.

    A blockface that merely touches the radius must not drag the rest of its
    street into the session, so it is cut back to the part that qualifies
    instead of being taken whole or dropped whole.
    """
    trimmed = (b.clipped_to_stops(reachable_ids) for b in store.blockfaces)
    return [b for b in trimmed if b is not None]


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        south, west, north, east = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="bbox must be south,west,north,east"
        ) from exc
    return south, west, north, east


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
        south, west, north, east = _parse_bbox(bbox)
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


@router.get("/stops")
def stops(
    request: Request,
    bbox: str | None = Query(default=None, description="south,west,north,east"),
    limit: int = Query(default=40_000, ge=1, le=100_000),
) -> dict:
    """Doors collapsed into the places a pair actually walks up to."""
    _require_snapshot(request)
    selected = _store(request).stops
    if bbox:
        south, west, north, east = _parse_bbox(bbox)
        selected = [
            s for s in selected if south <= s.lat <= north and west <= s.lon <= east
        ]
    truncated = len(selected) > limit
    selected = selected[:limit]
    return {
        "type": "FeatureCollection",
        "truncated": truncated,
        "count": len(selected),
        "doors": sum(s.door_count for s in selected),
        "features": [
            {
                "type": "Feature",
                "id": s.stop_id,
                "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                "properties": s.to_dict(),
            }
            for s in selected
        ],
    }


@router.get("/blockfaces")
def blockfaces(
    request: Request,
    bbox: str | None = Query(default=None, description="south,west,north,east"),
    street: str | None = Query(default=None, description="filter to one street name"),
    limit: int = Query(default=5_000, ge=1, le=20_000),
) -> dict:
    """The atomic units of work: one street, one side, one block."""
    _require_snapshot(request)
    selected = _store(request).blockfaces
    if street:
        wanted = normalise_street(street)
        selected = [b for b in selected if normalise_street(b.street) == wanted]
    if bbox:
        south, west, north, east = _parse_bbox(bbox)
        selected = [
            b
            for b in selected
            if south <= b.centroid[0] <= north and west <= b.centroid[1] <= east
        ]
    truncated = len(selected) > limit
    selected = selected[:limit]
    return {
        "type": "FeatureCollection",
        "truncated": truncated,
        "count": len(selected),
        "doors": sum(b.door_count for b in selected),
        "minutes": round(sum(b.minutes for b in selected), 1),
        "features": [
            {
                "type": "Feature",
                "id": b.blockface_id,
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[lon, lat] for lat, lon in chain] for chain in b.path],
                },
                "properties": {
                    **b.to_dict(),
                    "centroid": [b.centroid[1], b.centroid[0]],
                    "stop_ids": [s.stop_id for s in b.stops],
                },
            }
            for b in selected
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
    stops_within = [s for s in store.stops if haversine_m((lat, lon), s.point) <= radius_m]
    nearest = store.geocoder.nearest(lat, lon)

    walk_m = store.walk_graph.distances_from((lat, lon), store.address_snaps, radius_m)
    walkable = [a for a in snapshot.addresses if a.osm_id in walk_m]
    walkable_stops = [
        s for s in store.stops if any(d.osm_id in walk_m for d in s.doors)
    ]
    walkable_faces = _blockfaces_within(store, {s.stop_id for s in walkable_stops})
    return {
        "lat": lat,
        "lon": lon,
        "radius_m": radius_m,
        "inside_district": snapshot.contains((lat, lon)),
        "doors_within": len(within),
        "stops_within": len(stops_within),
        "streets_within": len({a.street for a in within}),
        "nearest_address": nearest.label if nearest else None,
        "effort": estimate_effort(len(within)),
        "walk": {
            "doors_within": len(walkable),
            "stops_within": len(walkable_stops),
            "streets_within": len({a.street for a in walkable}),
            "blockfaces_within": len(walkable_faces),
            "knock_hours": round(sum(s.dwell_seconds for s in walkable_stops) / 3600, 1),
            "minutes_to_farthest": round(max(walk_m.values()) / WALKING_SPEED_M_PER_MIN, 1)
            if walk_m
            else 0.0,
        },
    }


@router.get("/territories")
def territories(
    request: Request,
    lat: float,
    lon: float,
    teams: int = Query(ge=1, le=MAX_TEAMS),
    radius_m: float = Query(default=800, ge=50, le=3000),
) -> dict:
    """Splits the hub's walkable blockfaces into balanced team territories."""
    _require_snapshot(request)
    store = _store(request)
    walk_m = store.walk_graph.distances_from((lat, lon), store.address_snaps, radius_m)
    reachable_ids = {
        s.stop_id for s in store.stops if any(d.osm_id in walk_m for d in s.doors)
    }
    faces = _blockfaces_within(store, reachable_ids)
    plan = build_territories(faces, (lat, lon), teams)
    total_minutes = sum(t.minutes for t in plan.territories)
    features = []
    for territory in plan.territories:
        for b in territory.blockfaces:
            if b.path:
                geometry: dict = {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[pt_lon, pt_lat] for pt_lat, pt_lon in chain] for chain in b.path
                    ],
                }
            else:
                # Off-network fallbacks have no carriageway geometry, so they
                # draw as their stop points instead of vanishing.
                geometry = {
                    "type": "MultiPoint",
                    "coordinates": [[s.lon, s.lat] for s in b.stops],
                }
            features.append(
                {
                    "type": "Feature",
                    "id": b.blockface_id,
                    "geometry": geometry,
                    "properties": {
                        "team": territory.team,
                        "label": b.label,
                        "street": b.street,
                        "minutes": round(b.minutes, 1),
                        "doors": b.door_count,
                        "clipped": b.clipped,
                    },
                }
            )
    return {
        "type": "FeatureCollection",
        "lat": lat,
        "lon": lon,
        "radius_m": radius_m,
        "team_count": teams,
        "blockface_count": len(faces),
        "total_minutes": round(total_minutes, 1),
        "target_minutes": round(total_minutes / teams, 1),
        "spread_pct": round(plan.spread_pct * 100, 1),
        "split_streets": plan.split_streets,
        "teams": [t.to_dict() for t in plan.territories],
        "features": features,
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
