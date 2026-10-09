/**
 * WGS84 <-> local plan metres, the same equirectangular math as common/scada_common/geo.py:
 * plan (0, 0) sits at (origin_lat, origin_lon), the plan's y axis is rotated `rotation_deg`
 * clockwise from true north.
 */
export interface Georef {
  origin_lat: number
  origin_lon: number
  rotation_deg?: number
}

export const M_PER_DEG_LAT = 111_132.954

const rad = (deg: number) => (deg * Math.PI) / 180
const mPerDegLon = (g: Georef) => M_PER_DEG_LAT * Math.cos(rad(g.origin_lat))

/** Plan metres -> [lng, lat] (GeoJSON / MapLibre order). */
export function toLngLat(g: Georef, x: number, y: number): [number, number] {
  const r = rad(g.rotation_deg ?? 0)
  const east = x * Math.cos(r) + y * Math.sin(r)
  const north = -x * Math.sin(r) + y * Math.cos(r)
  return [g.origin_lon + east / mPerDegLon(g), g.origin_lat + north / M_PER_DEG_LAT]
}

/** [lng, lat] -> plan metres. */
export function toLocal(g: Georef, lng: number, lat: number): [number, number] {
  const east = (lng - g.origin_lon) * mPerDegLon(g)
  const north = (lat - g.origin_lat) * M_PER_DEG_LAT
  const r = rad(g.rotation_deg ?? 0)
  return [east * Math.cos(r) - north * Math.sin(r), east * Math.sin(r) + north * Math.cos(r)]
}

export const lngLatRing = (g: Georef, coords: [number, number][]) => coords.map(([x, y]) => toLngLat(g, x, y))
