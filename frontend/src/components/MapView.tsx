import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  Popup,
  ScaleControl,
  type ExpressionSpecification,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  type MapMouseEvent,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";

import type { AddressFeatureCollection, District, Territories, WalkRoute, Zones } from "../api";
import { circlePolygon } from "../geo";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

export const TEAM_COLORS = [
  "#e6194b",
  "#3cb44b",
  "#4363d8",
  "#f58231",
  "#911eb4",
  "#46f0f0",
  "#f032e6",
  "#9a6324",
];

/**
 * One colour per hue family, ordered so the first six - all a greedy colouring
 * ever reaches for on this district - are the most distinct.
 *
 * Chosen by maximising the smallest perceptual (CIE Lab) gap in the set rather
 * than by eye. The earlier palette held crimson, maroon and brown at once,
 * which read as three shades of red on a 4 px line. Yellow, olive and brown
 * are excluded on purpose: yellow has no dark saturated form, so at a
 * lightness that reads against a pale basemap it can only look muddy.
 *
 * Measured: smallest gap 33.7 across all eight and 40.3 across the first six;
 * every colour sits at least 56 from the basemap's own lightness, and no two
 * chromatic entries are within 27 degrees of hue.
 */
export const ZONE_COLORS = [
  "#d81b45", // crimson
  "#1a8f3c", // green
  "#1f5fc4", // blue
  "#00838f", // teal
  "#e8590c", // orange
  "#c2187e", // magenta
  "#8e44c9", // violet
  "#38424f", // slate
];

/**
 * Zones carry a `palette` slot chosen server-side by greedy graph colouring, so
 * no two touching zones share a colour. Doing it here would need a modulo over
 * the zone index, which a data-driven style expression cannot express, and
 * would put the same colour either side of a boundary.
 */
const ZONE_COLOR_EXPRESSION: ExpressionSpecification = [
  "match",
  ["get", "palette"],
  0, ZONE_COLORS[0],
  1, ZONE_COLORS[1],
  2, ZONE_COLORS[2],
  3, ZONE_COLORS[3],
  4, ZONE_COLORS[4],
  5, ZONE_COLORS[5],
  6, ZONE_COLORS[6],
  7, ZONE_COLORS[7],
  "#64748b",
];

const TEAM_COLOR_EXPRESSION: ExpressionSpecification = [
  "match",
  ["get", "team"],
  1, TEAM_COLORS[0],
  2, TEAM_COLORS[1],
  3, TEAM_COLORS[2],
  4, TEAM_COLORS[3],
  5, TEAM_COLORS[4],
  6, TEAM_COLORS[5],
  7, TEAM_COLORS[6],
  8, TEAM_COLORS[7],
  "#64748b",
];

const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
/**
 * OpenFreeMap: no API key, no usage limits, self-hostable.
 *
 * CARTO's raster tiles were dropped in August 2026 when CARTO began serving an
 * "API KEY REQUIRED" watermark in place of tiles its origin would not render
 * (open water first - Port Phillip Bay and Bass Strait - while land still
 * came back normally). Land tiles are next, and a basemap that degrades
 * without warning is not something to take into the field.
 *
 * OpenStreetMap's own tile server was the other keyless option and was
 * rejected: the OSMF tile usage policy asks apps not to use it.
 *
 * Attribution rides along in the source's TileJSON (OpenFreeMap, OpenMapTiles
 * and OpenStreetMap), so MapLibre renders the credit without help from us.
 *
 * These are vector tiles, roughly 120-180 KB each against CARTO's ~17 KB
 * rasters. That is the wrong direction for a phone on mobile data, and the
 * reason Phase 6's local `.pmtiles` extract matters more than it did.
 */

interface Props {
  district: District | null;
  addresses: AddressFeatureCollection | null;
  hub: { lat: number; lon: number } | null;
  radiusM: number;
  route: WalkRoute | null;
  routePoints: { lat: number; lon: number }[];
  territories: Territories | null;
  zones: Zones | null;
  showHouses: boolean;
  onPick: (lat: number, lon: number) => void;
}

export function MapView({
  district,
  addresses,
  hub,
  radiusM,
  route,
  routePoints,
  territories,
  zones,
  showHouses,
  onPick,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const onPickRef = useRef(onPick);
  const [ready, setReady] = useState(false);
  onPickRef.current = onPick;

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new MapLibreMap({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      center: [145.05, -37.8],
      zoom: 12,
    });
    mapRef.current = map;
    map.addControl(new NavigationControl({}), "top-right");
    map.addControl(new ScaleControl({ unit: "metric" }));

    map.on("load", () => {
      map.addSource("district", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "district-fill",
        type: "fill",
        source: "district",
        paint: { "fill-color": "#2563eb", "fill-opacity": 0.06 },
      });
      map.addLayer({
        id: "district-casing",
        type: "line",
        source: "district",
        paint: { "line-color": "#ffffff", "line-width": 6, "line-opacity": 0.85 },
      });
      map.addLayer({
        id: "district-line",
        type: "line",
        source: "district",
        paint: { "line-color": "#1d4ed8", "line-width": 3 },
      });

      map.addSource("radius", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "radius-fill",
        type: "fill",
        source: "radius",
        paint: { "fill-color": "#f59e0b", "fill-opacity": 0.12 },
      });
      map.addLayer({
        id: "radius-line",
        type: "line",
        source: "radius",
        paint: { "line-color": "#d97706", "line-width": 1.5 },
      });

      map.addSource("addresses", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "addresses-dots",
        type: "circle",
        source: "addresses",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 1.2, 14, 2.4, 17, 5],
          "circle-color": "#dc2626",
          "circle-opacity": 0.75,
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 15, 0, 17, 0.8],
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addSource("territories", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "territories-casing",
        type: "line",
        source: "territories",
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.7 },
      });
      map.addLayer({
        id: "territories-lines",
        type: "line",
        source: "territories",
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": TEAM_COLOR_EXPRESSION, "line-width": 5, "line-opacity": 0.85 },
      });
      map.addLayer({
        id: "territories-points",
        type: "circle",
        source: "territories",
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 5,
          "circle-color": TEAM_COLOR_EXPRESSION,
          "circle-opacity": 0.85,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addSource("zones", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "zone-boxes",
        type: "line",
        source: "zones",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "line-color": ZONE_COLOR_EXPRESSION,
          "line-width": 1.5,
          "line-opacity": 0.55,
          "line-dasharray": [3, 2],
        },
      });
      // A white casing under the colour, so a zone reads against whatever the
      // basemap draws underneath it.
      map.addLayer({
        id: "zone-casing",
        type: "line",
        source: "zones",
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.75 },
      });
      map.addLayer({
        id: "zone-lines",
        type: "line",
        source: "zones",
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ZONE_COLOR_EXPRESSION, "line-width": 4, "line-opacity": 0.95 },
      });
      map.addLayer({
        id: "zone-points",
        type: "circle",
        source: "zones",
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 4,
          "circle-color": ZONE_COLOR_EXPRESSION,
          "circle-opacity": 0.9,
          "circle-stroke-width": 1.2,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addSource("walk-route", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "walk-route-casing",
        type: "line",
        source: "walk-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "walk-route-line",
        type: "line",
        source: "walk-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#7c3aed", "line-width": 3.5 },
      });

      map.addSource("route-points", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "route-points-dots",
        type: "circle",
        source: "route-points",
        paint: {
          "circle-radius": 6,
          "circle-color": "#7c3aed",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      const popup = new Popup({ closeButton: false, closeOnClick: false });
      map.on("mouseenter", "territories-lines", (event: MapLayerMouseEvent) => {
        const feature = event.features?.[0];
        if (!feature) return;
        map.getCanvas().style.cursor = "pointer";
        const props = feature.properties ?? {};
        // The blockface label already carries doors and minutes. A clipped run
        // stops part-way along its block, so say so rather than let the
        // shortened line look like missing data.
        const trimmed = props.clipped ? " (trimmed at the radius)" : "";
        popup
          .setLngLat(event.lngLat)
          .setText(`Team ${props.team}: ${props.label}${trimmed}`)
          .addTo(map);
      });
      map.on("mouseleave", "territories-lines", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });
      map.on("mouseenter", "zone-lines", (event: MapLayerMouseEvent) => {
        const feature = event.features?.[0];
        if (!feature) return;
        map.getCanvas().style.cursor = "pointer";
        const props = feature.properties ?? {};
        popup.setLngLat(event.lngLat).setText(`${props.zone}: ${props.label}`).addTo(map);
      });
      map.on("mouseleave", "zone-lines", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });
      map.on("mouseenter", "addresses-dots", (event: MapLayerMouseEvent) => {
        const feature = event.features?.[0];
        if (!feature) return;
        map.getCanvas().style.cursor = "pointer";
        popup.setLngLat(event.lngLat).setText(String(feature.properties?.label ?? "")).addTo(map);
      });
      map.on("mouseleave", "addresses-dots", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });

      setReady(true);
    });

    map.on("click", (event: MapMouseEvent) => {
      const dots = map.queryRenderedFeatures(event.point, { layers: ["addresses-dots"] });
      const door = dots[0]?.geometry;
      if (door?.type === "Point") {
        onPickRef.current(door.coordinates[1], door.coordinates[0]);
        return;
      }
      onPickRef.current(event.lngLat.lat, event.lngLat.lng);
    });

    return () => {
      setReady(false);
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !district) return;
    const source = map.getSource("district") as GeoJSONSource | undefined;
    source?.setData({ type: "Feature", properties: {}, geometry: district.boundary });
    const [south, west, north, east] = district.bbox;
    map.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: 40, duration: 0 },
    );
  }, [ready, district]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !addresses) return;
    const source = map.getSource("addresses") as GeoJSONSource | undefined;
    source?.setData({ type: "FeatureCollection", features: addresses.features });
  }, [ready, addresses]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("radius") as GeoJSONSource | undefined;
    if (!source) return;
    if (!hub) {
      source.setData(EMPTY);
      map.setPaintProperty("addresses-dots", "circle-color", "#dc2626");
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    const circle = circlePolygon(hub.lat, hub.lon, radiusM);
    source.setData(circle);
    map.setPaintProperty("addresses-dots", "circle-color", [
      "case",
      ["within", circle],
      "#16a34a",
      "#dc2626",
    ]);
    if (markerRef.current) {
      markerRef.current.setLngLat([hub.lon, hub.lat]);
      return;
    }
    const marker = new Marker({ draggable: true, color: "#d97706" })
      .setLngLat([hub.lon, hub.lat])
      .addTo(map);
    marker.on("dragend", () => {
      const position = marker.getLngLat();
      onPickRef.current(position.lat, position.lng);
    });
    markerRef.current = marker;
  }, [ready, hub, radiusM]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("territories") as GeoJSONSource | undefined;
    source?.setData(
      territories
        ? { type: "FeatureCollection", features: territories.features as never[] }
        : EMPTY,
    );
  }, [ready, territories]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    // Hidden rather than emptied, so the source stays loaded and the dots come
    // straight back. Clicks still land: the map's click handler falls back to
    // the raw coordinate when no dot is under the cursor.
    map.setLayoutProperty("addresses-dots", "visibility", showHouses ? "visible" : "none");
  }, [ready, showHouses]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("zones") as GeoJSONSource | undefined;
    if (!zones) {
      source?.setData(EMPTY);
      return;
    }
    // The dashed bounding box shows the shape of the cut; the coloured lines
    // show which streets actually belong to it, which is what a volunteer
    // needs once the cut has been snapped to whole blockfaces.
    const boxes = zones.zones.map((zone) => {
      const [south, west, north, east] = zone.bbox;
      return {
        type: "Feature" as const,
        id: `${zone.id}/box`,
        geometry: {
          type: "Polygon" as const,
          coordinates: [
            [
              [west, south],
              [east, south],
              [east, north],
              [west, north],
              [west, south],
            ],
          ],
        },
        properties: { zone: zone.id, palette: zone.palette, label: zone.label },
      };
    });
    source?.setData({
      type: "FeatureCollection",
      features: [...boxes, ...zones.features] as never[],
    });
  }, [ready, zones]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const routeSource = map.getSource("walk-route") as GeoJSONSource | undefined;
    routeSource?.setData(
      route ? { type: "Feature", properties: {}, geometry: route.geometry } : EMPTY,
    );
    const pointsSource = map.getSource("route-points") as GeoJSONSource | undefined;
    pointsSource?.setData({
      type: "FeatureCollection",
      features: routePoints.map((p) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    });
  }, [ready, route, routePoints]);

  return <div ref={containerRef} className="map" />;
}
