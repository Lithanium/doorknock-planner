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
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";

import type { AddressFeatureCollection, District, Territories, WalkRoute } from "../api";
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

const BASEMAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    basemap: {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · © CARTO',
    },
  },
  layers: [{ id: "basemap", type: "raster", source: "basemap" }],
};

interface Props {
  district: District | null;
  addresses: AddressFeatureCollection | null;
  hub: { lat: number; lon: number } | null;
  radiusM: number;
  route: WalkRoute | null;
  routePoints: { lat: number; lon: number }[];
  territories: Territories | null;
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
        // The blockface label already carries doors and minutes.
        popup.setLngLat(event.lngLat).setText(`Team ${props.team}: ${props.label}`).addTo(map);
      });
      map.on("mouseleave", "territories-lines", () => {
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
