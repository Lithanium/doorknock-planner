import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  Popup,
  ScaleControl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  type MapMouseEvent,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";

import type { AddressFeatureCollection, District } from "../api";
import { circlePolygon } from "../geo";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

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
  onPick: (lat: number, lon: number) => void;
}

export function MapView({ district, addresses, hub, radiusM, onPick }: Props) {
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
        id: "district-line",
        type: "line",
        source: "district",
        paint: { "line-color": "#1d4ed8", "line-width": 2.5, "line-dasharray": [3, 2] },
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

      const popup = new Popup({ closeButton: false, closeOnClick: false });
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
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    source.setData(circlePolygon(hub.lat, hub.lon, radiusM));
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

  return <div ref={containerRef} className="map" />;
}
