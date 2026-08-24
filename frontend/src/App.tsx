import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  type AddressFeatureCollection,
  type Coverage,
  type District,
  type GeocodeCandidate,
  type Health,
  type HubPreview,
  type WalkRoute,
} from "./api";
import { MapView } from "./components/MapView";
import { formatNumber } from "./geo";

interface Hub {
  lat: number;
  lon: number;
  label: string;
}

const RADIUS_OPTIONS = [400, 600, 800, 1000, 1500];

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
  const searchToken = useRef(0);

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
          if (token === searchToken.current) setCandidates(r.candidates);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => {
          if (token === searchToken.current) setSearching(false);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  const refreshPreview = useCallback((lat: number, lon: number, radius: number) => {
    api
      .hubPreview(lat, lon, radius)
      .then(setPreview)
      .catch((e: Error) => setError(e.message));
  }, []);

  const chooseHub = useCallback(
    (lat: number, lon: number, label: string) => {
      setHub({ lat, lon, label });
      setCandidates([]);
      refreshPreview(lat, lon, radiusM);
    },
    [radiusM, refreshPreview],
  );

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
          refreshPreview(lat, lon, radiusM);
        })
        .catch((e: Error) => setError(e.message));
    },
    [routeMode, radiusM, refreshPreview],
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
    if (hub) refreshPreview(hub.lat, hub.lon, radiusM);
  }, [radiusM, hub, refreshPreview]);

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

        <footer className="muted">
          {addresses && <>{formatNumber(addresses.count)} pins drawn</>}
        </footer>
      </aside>

      <MapView
        district={district}
        addresses={addresses}
        hub={hub}
        radiusM={radiusM}
        route={route}
        routePoints={routePoints}
        onPick={pickFromMap}
      />
    </div>
  );
}
