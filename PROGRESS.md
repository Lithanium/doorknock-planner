# Progress

Doorknocking route planner for the Victorian Electoral District of Kew.
Read [AGENTS.md](AGENTS.md) first for commands, environment traps and measured
data facts. This file tracks **what is done, what is next, and how each stage
is proven to work**.

| Phase | Scope | Status |
| ----- | ----- | ------ |
| 0 | Skeleton you can run | **Done** |
| 1 | Real map data + hub picker, proven trustworthy | **Done** |
| 2 | Real walking distances | Not started |
| 3a | Collapse multi-unit clusters into stops | Not started |
| 3 | Blockfaces (the "human element", part 1) | Not started |
| 4 | Territories for N teams | Not started |
| 5 | Routing with pamphlet capacity and priority | Not started |
| 6 | Volunteer interface | Not started |
| 7 | Field hardening | Not started |

Current state: **149 backend tests + clean frontend typecheck and build.**

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

### Open question for the campaign

59 stops have 8+ doors and **378 Cotham Road alone has 95**. These are likely
apartment blocks with locked lobbies. Decide whether Phase 3 excludes them by
default or includes them with a manual skip. **Not yet answered.**

---

## Phase 2 — Real walking distances (Next)

Build a `networkx` walking graph from the 6,569 cached ways (include footway,
residential, path, steps and crossings; exclude `foot=no`, private access).
Snap each address to its nearest walkable edge with a door-to-footpath offset,
then compute travel times by multi-source Dijkstra and cache the result.

- Dependencies: `networkx==3.6.1` (pure Python, already version-checked).
- Do **not** attempt a full house-to-house matrix: 28,534² ≈ 792M cells ≈ 3 GB.
  Compute per-session matrices, and precompute district-wide only at the
  blockface level (~2-3k units ≈ 36 MB), which Phase 3 makes possible.

**Verify:** a debug mode where clicking two houses draws the actual walking
path with metres and minutes. Two houses either side of the Yarra, a railway
or Eastern Freeway must route via a real bridge or crossing — this single test
catches most snapping bugs. Plus unit tests on a hand-built graph.

---

## Phase 3a — Collapse multi-unit clusters into stops

Turn 28,534 doors into ~24,366 stops, each carrying `door_count` and
`dwell = approach + per_door x N`, flagging 8+ door clusters as probable gated
complexes. Groundwork exists: `geocode.spatial_clusters` and the coverage
report already compute this.

**Verify:** 378 Cotham Road appears as one stop with 95 doors and a dwell of
roughly two hours; 2A Kireep Road as one stop with 25.

---

## Phase 3 — Blockfaces (the human element)

Group stops into blockfaces: same street, same side, contiguous numbers,
between two intersections. Decide both-sides-in-one-pass versus one-side-per-
pass from road class and width. Blockfaces become **atomic** so the router can
never zigzag across a street.

> **Carry-over from Phase 1:** blockface keys must be street name **plus
> spatial cluster**, never name alone, or the two Mary Streets merge.

**Verify:** the map colours each blockface with a sidebar listing
`Smith St (even) #2-#48 - 24 doors - 11 min`. Eyeball against streets you know.

---

## Phase 4 — Territories for N teams

Balanced, **contiguous** partitioning of blockfaces into N territories weighted
by workload minutes, seeded outward from the hub, with no street split between
teams.

**Verify:** teams 1-8 produce N contiguous colour-coded areas with per-team
minutes within ~10%; no blockface in two territories (asserted).

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
