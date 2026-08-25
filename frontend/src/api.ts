import type {
  Feature,
  LineString,
  MultiLineString,
  MultiPoint,
  MultiPolygon,
  Point,
} from "geojson";

export interface Health {
  status: string;
  snapshot_available: boolean;
  district?: string;
  fetched_at?: string;
  doors?: number;
}

export interface District {
  name: string;
  relation_id: number;
  fetched_at: string;
  boundary_source: string;
  bbox: [number, number, number, number];
  boundary: MultiPolygon;
  doors: number;
  walkable_ways: number;
}

export interface AddressFeatureCollection {
  type: "FeatureCollection";
  count: number;
  truncated: boolean;
  features: Feature<Point, { label: string; street: string; number: string }>[];
}

export interface Coverage {
  district_name: string;
  fetched_at: string;
  doors: number;
  stops: number;
  streets: number;
  doors_with_unit: number;
  multi_unit_stops: number;
  gated_complex_candidates: number;
  largest_stops: { street: string; number: string; doors: number }[];
  cluster_histogram: Record<string, number>;
  addresses_missing_street: number;
  walkable_ways: Record<string, number>;
  boundary_rings: number;
  boundary_closed_rings: number;
  extent_km: [number, number];
  top_streets: { street: string; doors: number }[];
  effort: Effort;
}

export interface Effort {
  session_minutes: number;
  seconds_per_door: number;
  walking_overhead: number;
  doors_per_pair_session: number;
  pair_sessions_for_full_coverage: number;
}

export interface GeocodeCandidate {
  label: string;
  lat: number;
  lon: number;
  street: string;
  number: string | null;
  door_count: number;
  match_type: "exact" | "approximate" | "street" | "fuzzy";
  score: number;
  inside_district: boolean;
}

export interface HubPreview {
  lat: number;
  lon: number;
  radius_m: number;
  inside_district: boolean;
  doors_within: number;
  stops_within: number;
  streets_within: number;
  nearest_address: string | null;
  effort: Effort;
  walk: WalkReach;
}

export interface WalkReach {
  doors_within: number;
  stops_within: number;
  streets_within: number;
  minutes_to_farthest: number;
}

export interface WalkRoute {
  distance_m: number;
  minutes: number;
  crow_flies_m: number;
  detour_ratio: number;
  geometry: LineString;
}

export interface TerritoryTeam {
  team: number;
  minutes: number;
  doors: number;
  stops: number;
  blockfaces: number;
  streets: string[];
  contiguous: boolean;
}

export interface TerritoryFeature {
  type: "Feature";
  id: string;
  geometry: MultiLineString | MultiPoint;
  properties: {
    team: number;
    label: string;
    street: string;
    minutes: number;
    doors: number;
    /** Trimmed at the session radius, so the run stops part-way along its block. */
    clipped: boolean;
  };
}

export interface Territories {
  type: "FeatureCollection";
  lat: number;
  lon: number;
  radius_m: number;
  team_count: number;
  blockface_count: number;
  total_minutes: number;
  target_minutes: number;
  spread_pct: number;
  split_streets: string[];
  teams: TerritoryTeam[];
  features: TerritoryFeature[];
}

export interface RoutePlanMetrics {
  walk_km: number;
  walk_minutes: number;
  knock_minutes: number;
  walking_pct: number;
  knocking_pct: number;
  restock_trips: number;
  restock_minutes: number;
  total_minutes: number;
  session_minutes: number;
  doors_served: number;
  doors_dropped: number;
  blockfaces_served: number;
  blockfaces_dropped: number;
  coverage_pct: number;
  pamphlets_per_load: number;
  capacity_enabled: boolean;
}

export interface RoutePlanVisit {
  order: number;
  kind: "hub" | "reload" | "blockface";
  arrive_minute: number;
  pamphlets_left: number;
  blockface_id?: string;
  label?: string;
  street?: string;
  doors?: number;
  minutes?: number;
}

export interface RoutePlanDropped {
  blockface_id: string;
  label: string;
  street: string;
  doors: number;
  hub_distance_m: number;
}

export interface RoutePlan {
  lat: number;
  lon: number;
  radius_m: number;
  teams: number;
  team: number;
  metrics: RoutePlanMetrics;
  visits: RoutePlanVisit[];
  dropped: RoutePlanDropped[];
  geometry: LineString;
  served_faces: {
    type: "FeatureCollection";
    features: Feature<MultiLineString, { label: string; street: string }>[];
  };
}

export interface ReverseResult {
  label: string;
  lat: number;
  lon: number;
  distance_m: number;
  inside_district: boolean;
}

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    url.searchParams.set(key, String(value));
  }
  const response = await fetch(url);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  district: () => get<District>("/api/district"),
  addresses: () => get<AddressFeatureCollection>("/api/addresses"),
  coverage: () => get<Coverage>("/api/coverage"),
  geocode: (q: string) => get<{ candidates: GeocodeCandidate[] }>("/api/geocode", { q }),
  reverse: (lat: number, lon: number) => get<ReverseResult>("/api/reverse", { lat, lon }),
  hubPreview: (lat: number, lon: number, radius_m: number) =>
    get<HubPreview>("/api/hub/preview", { lat, lon, radius_m }),
  walkRoute: (from_lat: number, from_lon: number, to_lat: number, to_lon: number) =>
    get<WalkRoute>("/api/walk/route", { from_lat, from_lon, to_lat, to_lon }),
  territories: (lat: number, lon: number, teams: number, radius_m: number) =>
    get<Territories>("/api/territories", { lat, lon, teams, radius_m }),
  routePlan: (params: {
    lat: number;
    lon: number;
    radius_m: number;
    teams: number;
    team: number;
    pamphlets: number;
    session_minutes: number;
    capacity: string;
  }) => get<RoutePlan>("/api/route/plan", params),
};
