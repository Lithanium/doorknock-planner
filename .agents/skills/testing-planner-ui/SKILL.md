---
name: testing-planner-ui
description: How to run and browser-test the Doorknock Planner (FastAPI + Vite/React/MapLibre) locally, including the maplibre worker gotcha that makes all map overlays invisible in dev.
---

# Testing the Doorknock Planner UI

## Start
- `make dev` from repo root → API :8000, web :5173. Needs `data/district/kew.json.gz` (cached) and `.venv`.
- Health check: `curl localhost:8000/api/health` → `snapshot_available: true`, 28,534 doors.

## CRITICAL gotcha: invisible map overlays in `vite dev`
maplibre-gl v6+ loads its worker as a sibling module (`maplibre-gl-worker.mjs`) relative to
`import.meta.url`. Vite's dep prebundle rewrites the entry to
`/node_modules/.vite/deps/maplibre-gl.js`, so the worker URL 404s **silently** — the basemap
raster renders but every GeoJSON layer (border, dots, circle, routes) shows nothing, with no
console error and `map.loaded()` stuck false. Fix/workaround: in `frontend/vite.config.ts`
add `optimizeDeps: { exclude: ["maplibre-gl"] }` (or call `maplibregl.setWorkerUrl(...)`),
then hard-reload. If the map looks empty despite `/api/addresses` returning data, check
`curl -s -o /dev/null -w "%{http_code}" localhost:5173/node_modules/.vite/deps/maplibre-gl-worker.mjs` (404 = this bug).

## UI paths
- Hub: type ≥3 chars in the sidebar search (e.g. "Cotham Road"), click a candidate; or click the map (reverse-geocoded, snaps to an address dot under the cursor). Radius chips: 400/600/800/1000/1500 m.
- Walking route: click "Check a walking route" (becomes "Route mode on — click two houses"), then click two houses; purple line + distance/time/crow-flies/detour table. Third click starts a new pair. Toggling off clears the route.
- Good freeway-detour test pair (detour ×2.58, 702 m vs 272 m): -37.78681,145.07214 ↔ -37.78924,145.07178 (Balwyn North). Pre-verify via `GET /api/walk/route?from_lat=..&from_lon=..&to_lat=..&to_lon=..`.
- To click exact coords, temporarily expose `window.__map = map` in MapView.tsx and use `map.project([lon,lat])` + container rect (remember CSS px → screenshot-space scaling, e.g. ×1024/innerWidth, plus browser chrome y-offset).

## Notes
- Only external dependency at runtime: CARTO basemap tiles.
- No login/credentials needed.
