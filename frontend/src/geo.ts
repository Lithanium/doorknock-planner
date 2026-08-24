import type { Feature, Polygon } from "geojson";

const EARTH_RADIUS_M = 6_371_000;

export function circlePolygon(lat: number, lon: number, radiusM: number, steps = 96): Feature<Polygon> {
  const latRadius = (radiusM / EARTH_RADIUS_M) * (180 / Math.PI);
  const lonRadius = latRadius / Math.cos((lat * Math.PI) / 180);
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i += 1) {
    const angle = (i / steps) * 2 * Math.PI;
    ring.push([lon + lonRadius * Math.cos(angle), lat + latRadius * Math.sin(angle)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-AU");
}
