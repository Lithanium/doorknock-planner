# Doorknock Planner — working notes

Route planner for doorknocking the Victorian Electoral District of Kew
(Kew, Kew East, Balwyn, Balwyn North, Deepdene and surrounds).

## Commands

```bash
make setup            # venv + npm install
make fetch-district   # ONE-TIME Overpass extract -> data/district/kew.json.gz (~0.9 MB)
make report           # coverage report for the cached district
make dev              # API on :8000, web app on :5173
make test             # pytest (backend) + tsc --noEmit (frontend)
make build            # production frontend build
```

Run `make fetch-district` once before anything else. After that the planner is
fully offline; `backend/tests/test_offline.py` enforces this by blocking DNS,
TCP connect and httpx's network transports while exercising every endpoint.

Tests marked `snapshot` are skipped when no cached district exists, so `make
test` works on a fresh clone.

## Environment

- Python 3.14 (`.venv`), Node 20.18.
- **Node 20.18 cannot run vite 7 or 8** (they need `^20.19.0 || >=22.12.0`).
  Pinned to vite 6.4.3 + `@vitejs/plugin-react` 4.7.0. Vite 8 additionally
  fails to install its `@rolldown/binding-darwin-universal` native binary here.
- **Do not use `typescript@7`.** TypeScript 7 is the native Go port and ships
  only `tsc.js` — no `tsserver.js`, no `typescript.js`, no `lib.dom.d.ts` (5
  files in `lib/` versus 133 for v6). The CLI works, so `tsc --noEmit` passes,
  but editors cannot use it as a language server. They silently fall back to a
  mode where `tsconfig.json` is ignored, so `jsx: "react-jsx"` is not applied
  and every JSX tag reports `TS7026: JSX element implicitly has type 'any'
  because no interface 'JSX.IntrinsicElements' exists` — because React 19's
  types removed the global `JSX` namespace in favour of `React.JSX`.
  Pinned to `typescript@6.0.3`, with `.vscode/settings.json` pointing
  `typescript.tsdk` at the workspace copy so the editor and CLI always agree.
- Dependencies are pinned to exact versions at least 7 days old.

## Data source facts (measured, not assumed)

- District boundary is OSM relation **15624487**, `boundary=political`,
  `political_division=au_vic_la`. 112 member ways -> 1 closed ring, 5.2 km N-S
  x 9.6 km E-W. Close enough to the VEC boundary for planning purposes.
- **28,534 doors / 24,164 stops / 774 streets** inside the district.
  A bbox around the district gives 71,511 — it spills into Hawthorn and
  Camberwell, so always clip with `map_to_area`, never a bbox.
- Address coverage is effectively complete (Vicmap import): 100% have
  `addr:street`; only 1 record district-wide lacked one.
- Address data lives on **nodes**, not building polygons. Building footprints
  are sparse (~145 polygons per ~2,200 addresses), so "buildings without an
  address" is a misleading metric here.
- `addr:suburb` is missing on ~99% of records. Never filter or group by it.
- Street names are always spelled out in full ("Doncaster Road", never "Rd").
- 6,569 walkable ways (2,977 footway, 1,376 residential, 443 primary, ...).

## Gotchas that cost time

- **Overpass `out geom tags` silently drops relation members.** The boundary
  query must use `out geom` (body mode) or it returns zero geometry with no
  error. Guarded by a test.
- **Overpass is unreliable.** A single fetch hit 504, then 500, then 500 across
  three mirrors before succeeding on the 4th attempt. Mirror rotation with
  backoff is mandatory, not defensive padding.
- **Street names are not unique within the district.** There are two Mary
  Streets 5.8 km apart and two Henry Streets 5.0 km apart; 203 of 24,164 stops
  have records more than 150 m apart. Genuine multi-unit blocks never spread
  beyond 147 m (median 29 m), so 200 m single-link clustering separates the two
  cases cleanly. **Phase 3 blockface grouping must key on street name *plus*
  spatial cluster, not name alone.**
- **House numbers can be ranges** (`31-37 Harp Road`). Index both the full
  range and its leading number.
- **Suburb name ordering matters at street level.** Suburb-only lookups for
  "North Balwyn" and "Balwyn North" agree, but `Doncaster Road, North Balwyn`
  and `Doncaster Road, Balwyn North` resolve **1.6 km apart** via Nominatim.
  This is why geocoding is done locally against the district's own addresses.
- **Multi-unit stops are a big workload factor**: 4,441 doors carry a unit
  number, 1,759 stops have more than one door, 59 stops have 8+ doors, and
  378 Cotham Road alone has 95. A stop is not a door.

## Throughput reality

~93 doors per pair per 3-hour session (75 s/door, 35% walking overhead), so
full district coverage is ~307 pair-sessions. Prioritisation is therefore the
most valuable feature, not a fallback for running out of pamphlets.

## Remaining external dependency

Basemap raster tiles (CARTO) are still fetched live. Phase 6 should replace
them with a local `.pmtiles` extract for the district bbox so the app works
with no signal in the field.
