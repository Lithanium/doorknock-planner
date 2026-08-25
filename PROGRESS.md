# Progress

Doorknocking route planner for the Victorian Electoral District of Kew.
Read [AGENTS.md](AGENTS.md) first for commands, environment traps and measured
data facts. This file tracks **what is done, what is next, and how each stage
is proven to work**.

| Phase | Scope | Status |
| ----- | ----- | ------ |
| 0 | Skeleton you can run | **Done** |
| 1 | Real map data + hub picker, proven trustworthy | **Done** |
| 2 | Real walking distances | **Done** |
| 3a | Collapse multi-unit clusters into stops | **Done** |
| 3 | Blockfaces (the "human element", part 1) | **Backend done**, UI pending |
| 4 | Territories for N teams | **Done** |
| 5 | Routing with pamphlet capacity and priority | Not started |
| 6 | Volunteer interface | Not started |
| 7 | Field hardening | Not started |

Current state: **318 backend tests + clean frontend typecheck.** Phase 4 draws
colour-coded team territories on the map; Phase 3's own blockface layer (all
2,760 at once, unpartitioned) is still not rendered, but territories cover the
practical need: every reachable blockface is drawn once a team count is picked.

---

## The problem being solved

A **multi-trip capacitated vehicle routing problem** with a time budget and
prize-collecting:

- Vehicles are pairs of volunteers starting and ending at a pamphlet hub.
- Capacity is pamphlets carried; each door consumes `1 x take-up rate`, so
  exhausting the supply forces a hub return. Minimising that return walking is
  the core objective.
- Prizes are per-house priorities that fall with distance from the hub, so if
  supplies or daylight run out, the dropped houses are the far ones.
- A per-team session time budget keeps plans achievable.

**Why prioritisation matters more than optimisation:** the district holds
28,534 doors and a pair covers ~93 per 3-hour session, so full coverage is
~307 pair-sessions. The team will not knock everything. Choosing *which* doors
to skip is the highest-value feature in the app.

---

## Phase 0 — Skeleton (Done)

FastAPI backend (`backend/`), Vite + React + MapLibre frontend (`frontend/`),
`Makefile`, pinned dependencies, `.gitignore`, `.vscode/settings.json`.

**Verified:** `make dev` serves the API on :8000 and the app on :5173;
`/api/health` responds; `make test` and `make build` both pass.

---

## Phase 1 — Real map data (Done)

Departed from the original plan in one important way: **the whole district is
extracted once and cached**, rather than queried per session. One Overpass
fetch produces `data/district/kew.json.gz` (**870 KB**), after which the
planner makes no external calls at all.

### Built

| File | Role |
| ---- | ---- |
| `backend/app/osm/overpass.py` | Overpass client, mirror rotation, exponential backoff |
| `backend/app/osm/fetch.py` | The three district queries and their parsers |
| `backend/app/osm/boundary.py` | Ring assembly, ray-casting containment, haversine |
| `backend/app/osm/snapshot.py` | `Address` / `WalkWay` / `DistrictSnapshot`, gzip persistence |
| `backend/app/geocode.py` | Local geocoder over the district's own addresses |
| `backend/app/coverage.py` | Coverage report and effort estimates |
| `backend/app/services.py` | Lazy snapshot loading and derived indexes |
| `backend/app/api/routes.py` | `health`, `district`, `addresses`, `coverage`, `geocode`, `reverse`, `hub/preview` |
| `backend/app/cli.py` | `fetch-district`, `report` |
| `frontend/src/App.tsx` | Sidebar: hub search, radius, workload, coverage |
| `frontend/src/components/MapView.tsx` | District outline, 28,534 pins, draggable hub, radius ring |

### Verified

- 149 backend tests pass: `test_geocode` 46, `test_api` 21, `test_fetch` 16,
  `test_real_snapshot` 16, `test_boundary` 15, `test_coverage` 10,
  `test_snapshot` 7, `test_overpass` 8, `test_offline` 6, `test_services` 4.
- `test_offline.py` **proves the offline claim** by blocking DNS, TCP connect
  and httpx's network transports while exercising every endpoint. It leaves
  socket creation and the ASGI transport alone, because asyncio needs
  `socketpair()` and `TestClient` is itself an `httpx.Client`.
- `test_real_snapshot.py` runs against the real 28,534-address extract:
  boundary closes, extent matches ~5.2 x 9.6 km, every address has a street,
  every sampled address round-trips through the geocoder to within 150 m, and
  no candidate merges addresses kilometres apart.
- Real output confirmed by hand: 28,534 doors / 24,366 stops / 774 streets
  (stops keyed on street + number + spatial cluster, so the two Mary Streets
  never merge);
  2,134 doors within 800 m of Kew Junction; the `/api/addresses` payload is
  5.4 MB served in 33 ms.

### Three bugs found and fixed

1. **`out geom tags` silently returns no boundary geometry.** `tags` mode
   suppresses relation members, so the first fetch saved a district with no
   outline and no error. Now uses `out geom`, and an empty boundary raises.
2. **House numbers can be ranges.** `31-37 Harp Road` failed to geocode
   because the parser truncated it to `31`. Both forms are now indexed.
3. **Street names are not unique within the district.** Two Mary Streets
   5.8 km apart, two Henry Streets 5.0 km apart, 203 affected stops. The
   geocoder was averaging them into a meaningless midpoint; it now returns
   each location separately, labelled by nearest cross-street.

### Open question for the campaign — answered in Phase 3a

59 stops have 8+ doors and **378 Cotham Road alone has 95**. These are likely
apartment blocks with locked lobbies. **Answered:** include them, but charge a
capped dwell rather than 95 walk-ups. See Phase 3a below.

---

## Phase 2 — Real walking distances (Done)

Built as planned: a `networkx` walking graph from the 6,569 cached ways,
addresses snapped to their nearest walkable edge with a door-to-footpath
offset, travel times by Dijkstra at 80 m/min. No house-to-house matrix — the
graph and snaps are built lazily once per process and per-hub reachability is
computed on demand (`single_source_dijkstra_path_length` with a cutoff).

### Built

| File | Role |
| ---- | ---- |
| `backend/app/walkgraph.py` | `WalkGraph`: graph build, spatial grid, edge snapping, `route`, `distances_from` |
| `backend/app/services.py` | Lazy `walk_graph` / `address_snaps` on the snapshot store |
| `backend/app/api/routes.py` | `hub/preview` gains a `walk` block; new `GET /api/walk/route` |
| `frontend/src/App.tsx` | Walking-route check panel, walk stats, in-radius legend |
| `frontend/src/components/MapView.tsx` | Solid cased district border, route line, in-radius dot colouring |

- Walkability: include the cached highway classes; exclude `foot=no` and
  `access=private/no` unless `foot=yes/designated/permissive` overrides.
- Snapping uses a 0.0005° grid over edge segments with an equirectangular
  point-to-segment projection; snapping all 28,534 addresses takes ~0.8 s
  (graph build ~0.2 s), so nothing is persisted to disk.
- Addresses further than 300 m from any walkable way stay unsnapped rather
  than teleporting; routes and reachability simply exclude them.

### Verified

- 167 backend tests pass (18 new `test_walkgraph` on a hand-built graph where
  two parallel roads join only via one bridge, 4 new API tests, 4 new
  real-snapshot tests).
- The debug mode exists: toggle "Check a walking route", click two houses,
  and the actual path draws with metres, minutes and a detour factor.
- The freeway test passes against the real extract: 15 Aquila Street to
  49 Riverside Avenue (opposite sides of the Eastern Freeway, 272 m apart)
  routes 702 m (×2.58) via the Bulleen Road crossing, not straight across.
  Neighbouring houses on one street route near-directly.
- The real network is 96.9% one connected component; 99.9% of doors snap,
  median door-to-footpath offset ~10 m.
- `hub/preview` now reports crow-flies **and** walking reachability: 800 m
  around Kew Junction holds 2,134 doors by circle but only 1,277 within an
  800 m real walk (10 min to the farthest) — exactly the gap Phase 2 exists
  to expose.

### UI feedback folded in

- The Kew boundary is now a solid blue line over a white casing (was a thin
  dashed line that vanished into the basemap).
- Selecting a hub draws the radius circle and recolours the address dots:
  green inside the circle (pamphlets still needed), red outside, with a
  sidebar legend giving the in-circle door count.

---

## Phase 3a — Collapse multi-unit clusters into stops (Done)

**28,534 doors become 24,366 stops.** A stop is one place a pair physically
walks up to; every door behind it rides along. Routing over doors would invent
travel time between addresses that share a coordinate.

### Built

| File | Role |
| ---- | ---- |
| `backend/app/stops.py` | `Stop`, `build_stops`, `dwell_seconds`, `sort_key`; `stop_groups` moved here from `coverage.py` |
| `backend/app/services.py` | Lazy `stops` on the snapshot store |
| `backend/app/api/routes.py` | `GET /api/stops`; `hub/preview` counts stops and knocking hours |
| `backend/app/cli.py` | `report` prints blockface and knocking-hour lines |

### The gated-block decision

Include them, but **cap the dwell**: a 95-door tower is one buzzer panel, not
95 walk-ups. `dwell = approach + per_door x min(doors, 8)`, so 378 Cotham Road
costs 10.5 planning minutes instead of two hours. Capping *at* the 8-door gated
threshold makes dwell continuous — a 7-door stop and an 8-door stop cost almost
the same — so no stop becomes artificially attractive by having one fewer door.
`Stop.uncapped_dwell_seconds` keeps the honest figure, and the API exposes it
on every gated stop.

### Verified

- 378 Cotham Road: **one stop, 95 doors**, 10.5 min capped / **119 min
  uncapped** — the original "roughly two hours" figure, now labelled as such.
- 2A Kireep Road: **one stop, 25 doors**.
- Every door lands in exactly one stop; stop ids are stable when the snapshot
  is reordered (keyed on the lowest OSM id in the group, not on iteration
  order); no multi-unit stop spreads more than 200 m.

---

## Phase 3 — Blockfaces (Done, backend only)

**24,366 stops become 2,760 blockfaces**, the atomic units of work. Median 9
doors / 17 min, so a 3-hour pair-session is roughly ten of them.

### How a blockface is found

1. Every named walkable way is cut at its **intersections** — nodes shared with
   a *differently named* street. Unnamed footpaths and driveways are ignored,
   or every driveway in Kew would start a new blockface. This yields **2,557
   spans across 801 named streets**.
2. Each stop attaches to the nearest span *of its own street*, within 300 m.
3. Each span splits by side: **one blockface per side on arterials**, both
   sides together on quiet streets (the "minimise crossings on busy streets"
   rule — `primary`/`secondary`/`trunk`, or 4+ lanes, or 60 km/h+).
4. Runs longer than **45 minutes** are cut into contiguous parts in
   house-number order.

The Phase 1 carry-over is satisfied structurally rather than by convention:
span ids are derived from the span's own coordinates, so the two Mary Streets
can never share one however their names are spelled.

### Why blockfaces are capped at 45 minutes

Between-intersection runs alone left **265 blockfaces holding 10,563 doors —
37% of the district — in units of over 45 minutes**, topping out at Wiltshire
Drive's single 274-minute run, half again longer than a whole session. A unit
that big cannot be given to a team without blowing the budget. Splitting in
house-number order preserves everything atomicity exists to protect: one
street, one side, no zigzag, no street half-done by accident. p100 fell from
274 to 52 minutes.

### Built

| File | Role |
| ---- | ---- |
| `backend/app/blockface.py` | `StreetNetwork` (spans between intersections), `Blockface`, `is_busy`, `parity`, `split_into_parts` |
| `backend/app/walkgraph.py` | `node_key` / `project_to_segment` promoted from private, now shared |
| `backend/app/coverage.py` | Report gains blockface counts and knocking hours |
| `backend/app/api/routes.py` | `GET /api/blockfaces` (drawable `MultiLineString` + stop ids), street and bbox filters |

### Verified

- **294 backend tests** (66 new `test_blockface`, 23 new `test_stops`, 22 new
  real-snapshot, 12 new API, 2 new offline, 2 new services).
- Against the real extract: every one of the 24,366 stops lands in exactly one
  blockface, all 28,534 doors are accounted for, ids are unique, no blockface
  is empty, none mixes two streets, none spans more than 1 km end to end.
- Cotham Road becomes **56 blockfaces**, not one; every arterial blockface is
  marked one-side-per-pass; 90%+ of quiet residential runs are walked both
  sides at once.
- Labels read as intended: `Cotham Road (even) #2 to #30-38 - 13 doors - 23 min`.
- `/api/stops` 5.5 MB in 240 ms; `/api/blockfaces` 2.4 MB in 640 ms.

### Three bugs found

1. **Off-network blockfaces on arterials were marked zigzag-friendly.** Burke
   Road and Cotham Road have addresses inside the district whose carriageway is
   clipped out of the extract, so those stops fell back to a spatial-cluster
   blockface — which had no road tags and therefore looked like a quiet street.
   Volunteers would have been routed back and forth across an arterial. A
   fallback now inherits its street's class from any surviving way of that
   name, and a street with **no** geometry at all (Canterbury Road, 81 doors)
   is assumed busy: the cost of being wrong that way is a slightly inefficient
   route, against sending a pair across four lanes in the other.
2. **The 150 m street-match radius was too tight.** It dropped 349 stops onto
   the fallback path, including Wiltshire Drive at 152 m. Measured: 98% of
   stops sit within 89 m of their own street's centreline and the tail flattens
   past 300 m, which is already `walkgraph.MAX_SNAP_DISTANCE_M`. Aligning the
   two cut off-network doors from 626 to **459 (1.6%)**, all of them on clipped
   boundary roads.
3. **Two blocks of one street could silently merge into a single blockface.**
   Span ids were keyed on the span's lowest coordinate, but adjacent spans
   *share* the intersection node between them — so a V-shaped street whose
   junction sits at the bottom of the V gives both its spans the same anchor.
   Found by reasoning about the id scheme rather than by a failing test, then
   reproduced. It was live, not theoretical: fixing it recovered **3 real
   blockfaces** in Kew (2,757 -> 2,760). Ids now carry a suffix on collision,
   and spans are ordered by their lowest *edge*, which adjacent spans can
   never share.

### Open question for the campaign

The stop model puts full district coverage at **792 knocking hours**, against
the **594 h** implied by the existing 75 s/door headline. The gap is the 30 s
per-stop approach cost, which a per-door estimate never charged. Both figures
are reported (`knock_hours`, and `estimate_effort` unchanged) rather than one
silently overriding the other. `APPROACH_SECONDS` and `PER_DOOR_SECONDS` are
constants in `stops.py`; **calibrate them against a real session before Phase 5
uses them as routing costs.**

### Still to do for Phase 3

The map UI. `/api/blockfaces` returns per-blockface geometry, a label and its
stop ids, but nothing draws them yet. Original acceptance criterion stands:
colour each blockface on the map with a sidebar list, and eyeball it against
streets you know.

---

## Phase 4 — Territories for N teams (Done)

Balanced, **contiguous** partitioning of blockfaces into N territories weighted
by workload minutes, seeded outward from the hub, with no street split between
teams. `Blockface.minutes` is the weight to balance on, and blockfaces are now
small enough (median 17 min, max 52) for a ~10% balance to be achievable.

**Verify:** teams 1-8 produce N contiguous colour-coded areas with per-team
minutes within ~10%; no blockface in two territories (asserted).

### How a territory is grown

`territory.py` works on **street units**, not raw blockfaces: all of one
street's touching blockfaces (shared span, shared geometry node, or within
200 m) form one indivisible unit, so a street is never split between teams.
The only exception is a street bigger than `1.2 x` a team's whole share, which
is cut in house-number order and **reported in `split_streets`**, never hidden
(no street needs this at an 800 m radius on real data).

1. Units get an adjacency graph (shared intersection nodes, plus centroid
   proximity up to 250 m so parallel streets without a shared node still touch).
2. N seeds spread outward from the hub: nearest unit first, then repeatedly the
   unassigned unit farthest from all chosen seeds — spatially spread, hub-anchored.
3. Regions grow greedily: the lightest team claims its nearest adjacent unit,
   so territories stay connected while workloads stay level.
4. Boundary trades then flatten the result: a unit moves to a neighbouring
   team when that evens the pair out (variance objective with plateau moves,
   so equal-weight units can cascade across the map; the best layout seen is
   kept). A move that would cut the donor region in two is refused.

Disconnected pockets (off-network blockfaces with no walkable neighbours) are
handed whole to the lightest team, and that territory honestly reports
`contiguous: false` instead of pretending.

### Built

- `backend/app/territory.py` — `build_territories(blockfaces, hub, teams)`
  -> `TerritoryPlan` (territories, `split_streets`, `spread_pct`). Every input
  blockface lands in exactly one territory — **asserted in the builder**, not
  assumed.
- `GET /api/territories?lat&lon&teams&radius_m` — walkable blockfaces around
  the hub, split into teams, as a FeatureCollection with per-team summaries
  (minutes, doors, stops, streets, contiguous) and per-blockface features
  (`team`, `label`, `minutes`, `doors`). Off-network blockfaces draw as their
  stop points so they never silently vanish.
- Sidebar section "3. Team territories": off/1-8 chips, per-team legend with
  colour dots, minutes, doors, street count, spread %; map layers colour every
  blockface line (and off-network point) by team, with hover popups.

### Verified

- **318 backend tests** (24 new: 11 unit on a synthetic grid, 6 API, 1 offline,
  6 against the real snapshot).
- Real district, hub at 50 Cotham Road (Kew Junction), 800 m radius,
  178 blockfaces / ~50 knocking hours: teams 1-8 all contiguous, spread
  0.2% / 1.1% / 1.9% / 4.1% / 5.8% / 2.8% / 11.6% for 2-8 teams, no street
  split. 8 teams runs slightly over the ~10% target because whole streets
  are chunky at ~6 h/team shares; the test bound is 15%.
- Exactly-once assignment asserted for every team count 1-8 on real data;
  determinism asserted (same input -> byte-identical partition).
- Offline test proves `/api/territories` never touches the network.

---

## Phase 5 — Routing with capacity and priority

OR-Tools (`ortools==9.15.6755`, confirmed to have a cp314 arm64 wheel) with
depot-copy reload stops that reset the pamphlet dimension, configurable
pamphlets/take-up/dwell/speed/session length, disjunction penalties for
priority, and a human-smoothing pass penalising turns, street changes, major-
road crossings and re-walked segments.

**Verify:** no blockface split; restock stops appear exactly when the pamphlet
count hits N; near-hub houses come early; totals respect the session budget;
dropped houses are the farthest. A/B with capacity off must show restock
walking time drop. Golden-file test on a fixed area and seed. Metrics panel:
walk km, % walking vs knocking, restock trips, coverage %.

---

## Phase 6 — Volunteer interface

Mobile-first, no login. Pick "Team 3", get an ordered step list grouped by
street (`Smith St #2 -> #48 even side (24 doors)`, `Cross at the lights on
Main Rd`, `Restock at hub`), route line plus live GPS, tap-to-tick-off in
`localStorage`, and a print/PDF view as paper backup.

Also replace the CARTO raster basemap with a local `.pmtiles` extract for the
district bbox — the last remaining network dependency, and the one that matters
on a footpath with no signal.

**Verify:** open on a phone over LAN, pick a team, and **walk one block**.
Order should match what a sensible person would do; ticks survive a refresh.

---

## Phase 7 — Field hardening

Exclusion lists (gated blocks, do-not-contact, businesses), a units multiplier,
manual override (drag a street between teams and re-solve), mid-session re-plan
from remaining unticked stops, CSV/GPX export.

**Verify:** end-to-end run on the real target area at the real team count.

---

## Notes for a future session

- Run `make fetch-district` first on a fresh clone or every `snapshot`-marked
  test skips. `data/district/kew.json.gz` is intentionally **not** gitignored.
- The snapshot is dated in `meta.fetched_at`. The campaign runs to late
  November 2026, so staleness is a non-issue; no refresh strategy is needed.
- The district boundary is the OSM `au_vic_la` political relation, not the
  official VEC file. Confirmed adequate with the campaign ("rough range is
  enough").
- Do not reintroduce a remote geocoder. Local geocoding is what makes
  `Doncaster Road, North Balwyn` versus `Balwyn North` (1.6 km apart via
  Nominatim) impossible to get wrong.
