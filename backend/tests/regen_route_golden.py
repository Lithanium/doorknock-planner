"""Regenerate the Phase 5 golden route file after a deliberate routing change.

Run from `backend/`:  ../.venv/bin/python -m tests.regen_route_golden
"""
from __future__ import annotations

import json
from pathlib import Path

from app.blockface import build_blockfaces
from app.config import load_settings
from app.osm.boundary import haversine_m
from app.osm.snapshot import DistrictSnapshot
from app.routing import RouteConfig, plan_route
from app.stops import build_stops

HUB = (-37.8071644, 145.0320872)  # 50 Cotham Road, near Kew Junction
RADIUS_M = 400
CONFIG = RouteConfig(pamphlets=150, session_minutes=180)


def main() -> None:
    snapshot = DistrictSnapshot.load(load_settings().snapshot_path)
    faces = build_blockfaces(build_stops(snapshot.addresses), snapshot.ways)
    reachable = {
        s.stop_id
        for b in faces
        for s in b.stops
        if haversine_m((s.lat, s.lon), HUB) <= RADIUS_M
    }
    trimmed = [c for c in (b.clipped_to_stops(reachable) for b in faces) if c]
    plan = plan_route(trimmed, HUB, CONFIG)
    golden = {
        "hub": list(HUB),
        "radius_m": RADIUS_M,
        "config": {
            "pamphlets": CONFIG.pamphlets,
            "session_minutes": CONFIG.session_minutes,
        },
        "visits": [
            {
                "kind": v.kind,
                "blockface_id": v.blockface.blockface_id if v.blockface else None,
                "arrive_minute": round(v.arrive_minute, 1),
                "pamphlets_left": v.pamphlets_left,
            }
            for v in plan.visits
        ],
        "dropped": [f.blockface_id for f in plan.dropped],
        "metrics": plan.metrics(),
    }
    path = Path(__file__).parent / "golden_route_plan.json"
    path.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path}: {len(golden['visits'])} visits, {plan.metrics()['coverage_pct']}% coverage")


if __name__ == "__main__":
    main()
