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
- **28,534 doors / 24,366 stops / 774 streets** inside the district. Stops
  are keyed on street name + number + spatial cluster, so same-named streets
  kilometres apart never merge.
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
  Streets 5.8 km apart and two Henry Streets 5.0 km apart; 203 of the stops
  have records more than 150 m apart. Genuine multi-unit blocks never spread
  beyond 147 m (median 29 m), so 200 m single-link clustering separates the two
  cases cleanly. Blockface grouping must key on street name *plus* spatial
  cluster, never name alone — `blockface.py` gets this structurally, by
  deriving each span's id from the span's own coordinates.
- **A T-junction splits the street it runs into, not only the street that
  ends.** Both sides of the junction node become separate blocks. Correct, and
  surprising the first time you assert otherwise in a test.
- **Adjacent spans of one street share the intersection node between them**,
  so a span id keyed on "lowest coordinate in the span" is not unique: a
  V-shaped street with the junction at the bottom of the V gives both spans the
  same anchor and merges two blocks. Three real Kew blockfaces were lost this
  way. Ids are deduped with a suffix and spans ordered by lowest *edge*, which
  adjacent spans can never share.
- **A blockface must inherit its street's road class even when the geometry is
  missing.** Boundary roads (Burke, Barkers, Canterbury) have addresses inside
  the district but carriageway outside it, so their stops fall back to
  cluster-grouping. A fallback carrying no road tags looks like a quiet street
  and will happily route a pair back and forth across four lanes. Streets with
  no geometry at all are assumed busy on purpose.
- **House numbers can be ranges** (`31-37 Harp Road`). Index both the full
  range and its leading number. They also turn up as blockface endpoints, where
  `#2-#30-38` is unreadable and has to be rendered `#2 to #30-38`.
- **Suburb name ordering matters at street level.** Suburb-only lookups for
  "North Balwyn" and "Balwyn North" agree, but `Doncaster Road, North Balwyn`
  and `Doncaster Road, Balwyn North` resolve **1.6 km apart** via Nominatim.
  This is why geocoding is done locally against the district's own addresses.
- **Multi-unit stops are a big workload factor**: 4,441 doors carry a unit
  number, **1,592** stops have more than one door, 59 stops have 8+ doors, and
  378 Cotham Road alone has 95. A stop is not a door. (An earlier note here
  said 1,759; that was never what the code produced. Re-measured 2026-08-24.)
- **811 named walkable ways carry 801 distinct street names**, which cut into
  **2,557 spans** between intersections and then **2,760 blockfaces** (median
  9 doors / 17 min). 15 streets have addresses but no named way at all;
  459 doors (1.6%) sit further than 300 m from any way of their own street,
  almost all on boundary roads whose carriageway is clipped out of the extract.
- Every address starts with a digit, so even/odd side-of-street parity never
  has to guess. The split is near-even: 14,203 even, 14,331 odd.
- 1,227 ways tag `maxspeed` (683 are 50, 433 are 60) and 772 tag `lanes`,
  which is enough to classify arterials without guessing from road class alone.
- **Greedy region-growing balances badly on its own; pure variance-descent
  rebalancing stalls on equal-weight units.** Growing from hub-spread seeds can
  leave a 50% workload spread. Boundary trades fix it, but when a heavy and a
  light territory never touch, work has to cascade through a middle team, and
  that intermediate move is variance-*neutral* (near-equal unit weights), so a
  strict-improvement rule never takes it. `territory.py` accepts plateau moves
  (within `_PLATEAU_EPS` minutes of even, heavier -> lighter only, with a
  don't-move-straight-back rule) and keeps the best layout seen. Real-district
  spread at 800 m / 50 Cotham Road: <= 6% for 2-6 teams, 11.6% at 8, where
  whole-street units get chunky relative to a team's share.
- **A street is a unit, not a blockface, when carving territories.** "No street
  split between teams" means grouping each street's touching blockfaces first;
  only a street bigger than 1.2x a team's whole share is cut (in house-number
  order), and it is reported in `split_streets`, never silently.
- **The session radius must clip blockfaces, not select them.** A blockface
  that merely touches the radius used to be taken whole, walking a pair down
  the rest of the street: at a 100 m radius, 42 of 59 assigned stops were
  *outside* it. `Blockface.clipped_to_stops` trims the run, its geometry and
  its walking length together. Radius options are 100-800 m; 1 km+ is too far
  from a hub to walk.
- **Trimming geometry must not trim topology.** `Blockface.network_nodes` keeps
  the untrimmed span's nodes precisely because territory adjacency is a fact
  about the street network, not about this session's extents. Derive adjacency
  from `path` instead and every clipped boundary reads as a gap, which makes
  every territory non-contiguous.
- **Territory scope is the crow-flies circle, not walking distance.** A stop
  just past a walk cutoff still needs its pamphlet, and gating on walking
  leaves holes mid-plan. `/api/hub/preview` still reports *walking*
  reachability, so the two panels legitimately disagree: at 800 m round Kew
  Junction the circle holds 2,101 doors against ~1,354 within an 800 m walk.
  Walking can never beat the straight line, so territories are always a
  superset — that is the invariant the API test asserts.
- **Territory balance is fine at 800 m and hopeless in a small circle.** Worst
  spread over 2-8 teams: Kew Junction 3.8%, Balwyn North 3.3%, Kew East 10.8%
  at 800 m; but 55.7% / 125.4% / 138.0% at 200 m, where a session holds only
  15-31 blockfaces. **Team count must scale with radius** — the search cannot
  divide work that is not there. Do not chase small-radius balance by
  loosening the 10% test or by splitting streets.
- Balance numbers move a lot with scope, so re-measure before quoting them.
  Under the earlier walking-distance scope these same hubs read 22.7% / 30.4% /
  19.1%, and Phase 4's original "~10%" claim came from testing one hub only.

## Throughput reality

~93 doors per pair per 3-hour session (75 s/door, 35% walking overhead), so
full district coverage is ~307 pair-sessions. Prioritisation is therefore the
most valuable feature, not a fallback for running out of pamphlets.

**Two dwell models coexist and disagree; neither is calibrated.**
`estimate_effort` charges 75 s per door and implies 594 knocking hours for the
district. `stops.dwell_seconds` charges 30 s per *stop* plus 75 s per door,
which the per-door model never billed for, and reports 792 hours. Both are
served (`effort` and `knock_hours` on `/api/coverage`) rather than one quietly
winning. `APPROACH_SECONDS` and `PER_DOOR_SECONDS` live in `stops.py` — time a
real session and fix them before Phase 5 turns them into routing costs.

Gated blocks are charged a capped dwell: `approach + per_door x min(doors, 8)`,
so 378 Cotham Road costs 10.5 min, not 2 hours. The cap sits exactly at the
8-door gated threshold so dwell stays continuous and no stop is made cheap by
losing one door. `Stop.uncapped_dwell_seconds` keeps the real figure.

## Remaining external dependency

Basemap raster tiles (CARTO) are still fetched live. Phase 6 should replace
them with a local `.pmtiles` extract for the district bbox so the app works
with no signal in the field.
