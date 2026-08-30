import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  type AddressFeatureCollection,
  type Coverage,
  type District,
  type GeocodeCandidate,
  type Health,
  type HubPreview,
  type Territories,
  type WalkRoute,
  type Zones,
} from "./api";
import { MapView, TEAM_COLORS } from "./components/MapView";
import { formatNumber } from "./geo";

interface Hub {
  lat: number;
  lon: number;
  label: string;
}

const RADIUS_OPTIONS = [100, 200, 400, 600, 800];
const TEAM_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8];
const ZONE_TARGETS = [0, 400, 500, 600, 700, 800];

/** The middle of the pack plus the extremes: a mean would hide the outliers. */
function zoneDoorRange(zones: Zones): string {
  const doors = zones.zones.map((z) => z.doors).sort((a, b) => a - b);
  if (doors.length === 0) return "-";
  const median = doors[Math.floor(doors.length / 2)];
  return `${doors[0]}-${doors[doors.length - 1]}, median ${median}`;
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [district, setDistrict] = useState<District | null>(null);
  const [addresses, setAddresses] = useState<AddressFeatureCollection | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<GeocodeCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [hub, setHub] = useState<Hub | null>(null);
  const [radiusM, setRadiusM] = useState(800);
  const [preview, setPreview] = useState<HubPreview | null>(null);
  const [routeMode, setRouteMode] = useState(false);
  const [routePoints, setRoutePoints] = useState<{ lat: number; lon: number }[]>([]);
  const [route, setRoute] = useState<WalkRoute | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [teams, setTeams] = useState(0);
  const [territories, setTerritories] = useState<Territories | null>(null);
  const [territoriesLoading, setTerritoriesLoading] = useState(false);
  const [showHouses, setShowHouses] = useState(true);
  const [zoneTarget, setZoneTarget] = useState(0);
  const [zones, setZones] = useState<Zones | null>(null);
  const [zonesLoading, setZonesLoading] = useState(false);
  const searchToken = useRef(0);
  const territoryToken = useRef(0);
  const zoneToken = useRef(0);

  useEffect(() => {
    api
      .health()
      .then(async (h) => {
        setHealth(h);
        if (!h.snapshot_available) return;
        const [d, a, c] = await Promise.all([api.district(), api.addresses(), api.coverage()]);
        setDistrict(d);
        setAddresses(a);
        setCoverage(c);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (query.trim().length < 3) {
      setCandidates([]);
      return;
    }
    const token = ++searchToken.current;
    setSearching(true);
    const timer = setTimeout(() => {
      api
        .geocode(query)
        .then((r) => {
          if (token === searchToken.current) {
            setCandidates(r.candidates);
            setError(null);
          }
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => {
          if (token === searchToken.current) setSearching(false);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  const chooseHub = useCallback((lat: number, lon: number, label: string) => {
    setHub({ lat, lon, label });
    setCandidates([]);
  }, []);

  const pickFromMap = useCallback(
    (lat: number, lon: number) => {
      if (routeMode) {
        setRoutePoints((points) => {
          const next = points.length >= 2 ? [{ lat, lon }] : [...points, { lat, lon }];
          if (next.length === 2) {
            setRouteError(null);
            api
              .walkRoute(next[0].lat, next[0].lon, next[1].lat, next[1].lon)
              .then(setRoute)
              .catch((e: Error) => {
                setRoute(null);
                setRouteError(e.message);
              });
          } else {
            setRoute(null);
            setRouteError(null);
          }
          return next;
        });
        return;
      }
      api
        .reverse(lat, lon)
        .then((r) => {
          setHub({ lat, lon, label: `${r.label} (${Math.round(r.distance_m)} m away)` });
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    },
    [routeMode],
  );

  const toggleRouteMode = useCallback(() => {
    setRouteMode((on) => {
      if (on) {
        setRoutePoints([]);
        setRoute(null);
        setRouteError(null);
      }
      return !on;
    });
  }, []);

  useEffect(() => {
    if (!hub) return;
    api
      .hubPreview(hub.lat, hub.lon, radiusM)
      .then((p) => {
        setPreview(p);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [radiusM, hub]);

  useEffect(() => {
    const token = ++territoryToken.current;
    if (!hub || teams === 0) {
      setTerritories(null);
      setTerritoriesLoading(false);
      return;
    }
    setTerritoriesLoading(true);
    api
      .territories(hub.lat, hub.lon, teams, radiusM)
      .then((t) => {
        if (token !== territoryToken.current) return;
        setTerritories(t);
        setError(null);
      })
      .catch((e: Error) => {
        if (token !== territoryToken.current) return;
        setTerritories(null);
        setError(e.message);
      })
      .finally(() => {
        if (token === territoryToken.current) setTerritoriesLoading(false);
      });
  }, [teams, radiusM, hub]);

  useEffect(() => {
    const token = ++zoneToken.current;
    if (zoneTarget === 0) {
      setZones(null);
      setZonesLoading(false);
      return;
    }
    setZonesLoading(true);
    api
      .zones(zoneTarget)
      .then((z) => {
        if (token !== zoneToken.current) return;
        setZones(z);
        setError(null);
      })
      .catch((e: Error) => {
        if (token !== zoneToken.current) return;
        setZones(null);
        setError(e.message);
      })
      .finally(() => {
        if (token === zoneToken.current) setZonesLoading(false);
      });
  }, [zoneTarget]);

  if (health && !health.snapshot_available) {
    return (
      <div className="setup">
        <h1>Doorknock Planner</h1>
        <p>No district data cached yet. Run this once:</p>
        <pre>make fetch-district</pre>
        <p>
          It downloads the whole electorate in a single Overpass query (~0.9 MB on disk). After
          that the app never contacts the internet again for map data.
        </p>
      </div>
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <header>
          <h1>Doorknock Planner</h1>
          {district && (
            <p className="muted">
              {district.name}
              <br />
              snapshot {district.fetched_at.slice(0, 10)} · boundary from {district.boundary_source}
            </p>
          )}
        </header>

        {error && <div className="error">{error}</div>}

        <section>
          <h2>1. Pamphlet hub</h2>
          <input
            className="search"
            value={query}
            placeholder="e.g. 22 Yerrin St, or Cotham Road"
            onChange={(event) => setQuery(event.target.value)}
            autoComplete="off"
          />
          {searching && <p className="muted">searching…</p>}
          {candidates.length > 0 && (
            <ul className="candidates">
              {candidates.map((candidate) => (
                <li key={`${candidate.label}-${candidate.lat}-${candidate.lon}`}>
                  <button onClick={() => chooseHub(candidate.lat, candidate.lon, candidate.label)}>
                    <span className="candidate-label">{candidate.label}</span>
                    <span className={`tag tag-${candidate.match_type}`}>{candidate.match_type}</span>
                    {!candidate.inside_district && <span className="tag tag-warn">outside district</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {query.trim().length >= 3 && !searching && candidates.length === 0 && (
            <p className="muted">No match in this district.</p>
          )}
          <p className="hint">Or click the map. Drag the marker to fine-tune.</p>
        </section>

        {hub && (
          <section>
            <h2>2. Session area</h2>
            <p className="hub-label">{hub.label}</p>
            <p className="muted">
              {hub.lat.toFixed(5)}, {hub.lon.toFixed(5)}
            </p>
            <div className="radios">
              {RADIUS_OPTIONS.map((option) => (
                <button
                  key={option}
                  className={option === radiusM ? "chip chip-on" : "chip"}
                  onClick={() => setRadiusM(option)}
                >
                  {option} m
                </button>
              ))}
            </div>
            {preview && (
              <>
                <p className="legend">
                  <span className="dot dot-in" /> {formatNumber(preview.doors_within)} doors in this
                  circle still need pamphlets
                </p>
                <table className="stats">
                  <tbody>
                    <tr>
                      <th>Doors in range</th>
                      <td>{formatNumber(preview.doors_within)}</td>
                    </tr>
                    <tr>
                      <th>Stops (street + number)</th>
                      <td>{formatNumber(preview.stops_within)}</td>
                    </tr>
                    <tr>
                      <th>Streets</th>
                      <td>{formatNumber(preview.streets_within)}</td>
                    </tr>
                    <tr>
                      <th>Doors walkable in radius</th>
                      <td>{formatNumber(preview.walk.doors_within)}</td>
                    </tr>
                    <tr>
                      <th>Walk to farthest door</th>
                      <td>{preview.walk.minutes_to_farthest} min</td>
                    </tr>
                    <tr>
                      <th>Pair-sessions needed</th>
                      <td>~{preview.effort.pair_sessions_for_full_coverage}</td>
                    </tr>
                    <tr>
                      <th>Hub inside district</th>
                      <td>{preview.inside_district ? "yes" : "NO"}</td>
                    </tr>
                  </tbody>
                </table>
              </>
            )}
          </section>
        )}

        {hub && (
          <section>
            <h2>3. Team territories</h2>
            <div className="radios">
              {TEAM_OPTIONS.map((option) => (
                <button
                  key={option}
                  className={option === teams ? "chip chip-on" : "chip"}
                  onClick={() => setTeams(option)}
                >
                  {option === 0 ? "off" : option}
                </button>
              ))}
            </div>
            {territoriesLoading && <p className="muted">carving territories…</p>}
            {territories && !territoriesLoading && (
              <>
                <p className="muted">
                  {territories.blockface_count} blockfaces ·{" "}
                  {formatNumber(Math.round(territories.total_minutes))} min total · target{" "}
                  {formatNumber(Math.round(territories.target_minutes))} min/team · spread{" "}
                  {territories.spread_pct}%
                </p>
                <table className="stats">
                  <tbody>
                    {territories.teams.map((team) => (
                      <tr key={team.team}>
                        <th>
                          <span
                            className="dot"
                            style={{ background: TEAM_COLORS[team.team - 1] }}
                          />{" "}
                          Team {team.team}
                        </th>
                        <td>
                          {formatNumber(Math.round(team.minutes))} min · {formatNumber(team.doors)}{" "}
                          doors · {team.streets.length} streets
                          {!team.contiguous && " · split area"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {territories.split_streets.length > 0 && (
                  <p className="hint">
                    Split between teams: {territories.split_streets.join(", ")}
                  </p>
                )}
              </>
            )}
          </section>
        )}

        <section>
          <h2>4. Electorate zones</h2>
          <p className="muted">
            Cuts the whole district into connected patches of roughly this many doors,
            independently of any hub.
          </p>
          <div className="radios">
            {ZONE_TARGETS.map((option) => (
              <button
                key={option}
                className={option === zoneTarget ? "chip chip-on" : "chip"}
                onClick={() => setZoneTarget(option)}
              >
                {option === 0 ? "off" : `${option} doors`}
              </button>
            ))}
          </div>
          {zonesLoading && <p className="muted">cutting zones…</p>}
          {zones && !zonesLoading && (
            <>
              <table className="stats">
                <tbody>
                  <tr>
                    <th>Zones</th>
                    <td>{formatNumber(zones.zone_count)}</td>
                  </tr>
                  <tr>
                    <th>Doors per zone</th>
                    <td>
                      {zoneDoorRange(zones)} (target {zones.target_doors})
                    </td>
                  </tr>
                  <tr>
                    <th>Doors covered</th>
                    <td>
                      {formatNumber(zones.covered_doors)} of {formatNumber(zones.total_doors)} (
                      {zones.coverage_pct}%)
                    </td>
                  </tr>
                  <tr>
                    <th>Left out</th>
                    <td>
                      {formatNumber(zones.dropped_doors)} doors in {zones.dropped_blockfaces}{" "}
                      stranded runs
                    </td>
                  </tr>
                </tbody>
              </table>
              <p className="hint">
                Dashed boxes show each cut; solid lines are the streets that ended up in it.
                {zones.split_streets.length > 0 &&
                  ` ${zones.split_streets.length} streets are longer than one zone and span two.`}
              </p>
            </>
          )}
        </section>

        <section>
          <h2>Walking route check</h2>
          <button className={routeMode ? "chip chip-on" : "chip"} onClick={toggleRouteMode}>
            {routeMode ? "Route mode on — click two houses" : "Check a walking route"}
          </button>
          {routeMode && (
            <p className="hint">
              Click two houses on the map to see the real walking path between them
              {routePoints.length === 1 && " — one more to go"}.
            </p>
          )}
          {routeError && <div className="error">{routeError}</div>}
          {route && (
            <table className="stats">
              <tbody>
                <tr>
                  <th>Walking distance</th>
                  <td>{formatNumber(Math.round(route.distance_m))} m</td>
                </tr>
                <tr>
                  <th>Walking time</th>
                  <td>{route.minutes} min</td>
                </tr>
                <tr>
                  <th>As the crow flies</th>
                  <td>{formatNumber(Math.round(route.crow_flies_m))} m</td>
                </tr>
                <tr>
                  <th>Detour factor</th>
                  <td>×{route.detour_ratio}</td>
                </tr>
              </tbody>
            </table>
          )}
        </section>

        {coverage && (
          <section>
            <h2>District data</h2>
            <table className="stats">
              <tbody>
                <tr>
                  <th>Doors</th>
                  <td>{formatNumber(coverage.doors)}</td>
                </tr>
                <tr>
                  <th>Stops</th>
                  <td>{formatNumber(coverage.stops)}</td>
                </tr>
                <tr>
                  <th>Streets</th>
                  <td>{formatNumber(coverage.streets)}</td>
                </tr>
                <tr>
                  <th>Multi-unit stops</th>
                  <td>{formatNumber(coverage.multi_unit_stops)}</td>
                </tr>
                <tr>
                  <th>Likely gated blocks</th>
                  <td>{formatNumber(coverage.gated_complex_candidates)}</td>
                </tr>
                <tr>
                  <th>Addresses missing a street</th>
                  <td>{coverage.addresses_missing_street}</td>
                </tr>
                <tr>
                  <th>Extent</th>
                  <td>
                    {coverage.extent_km[0]} × {coverage.extent_km[1]} km
                  </td>
                </tr>
              </tbody>
            </table>
            <details>
              <summary>Biggest stops</summary>
              <ul className="plain">
                {coverage.largest_stops.map((stop) => (
                  <li key={`${stop.number}-${stop.street}`}>
                    {stop.number} {stop.street} — <strong>{stop.doors} doors</strong>
                  </li>
                ))}
              </ul>
            </details>
          </section>
        )}

        <section>
          <h2>Map display</h2>
          <button
            className={showHouses ? "chip chip-on" : "chip"}
            onClick={() => setShowHouses((on) => !on)}
          >
            {showHouses ? "House dots on" : "House dots off"}
          </button>
          <p className="hint">
            {showHouses
              ? "Hide the house dots to read the zone and territory colours more clearly."
              : "Dots hidden. Clicking the map still sets a hub, but snaps to the nearest address rather than the one under the cursor."}
          </p>
        </section>

        <footer className="muted">
          {addresses && (
            <>
              {formatNumber(addresses.count)} pins {showHouses ? "drawn" : "hidden"}
            </>
          )}
        </footer>
      </aside>

      <MapView
        district={district}
        addresses={addresses}
        hub={hub}
        radiusM={radiusM}
        route={route}
        routePoints={routePoints}
        territories={territories}
        zones={zones}
        showHouses={showHouses}
        onPick={pickFromMap}
      />
    </div>
  );
}
