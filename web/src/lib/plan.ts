import L from 'leaflet'
import type { InjectionKey, ShallowRef } from 'vue'

/** Plan metres -> Leaflet CRS.Simple: lat = y (north), lng = x (east). */
export const ll = (x: number, y: number): L.LatLngTuple => [y, x]
export const ring = (coords: [number, number][]): L.LatLngTuple[] => coords.map(([x, y]) => ll(x, y))

export function boundsOf(extent: [number, number, number, number]): L.LatLngBounds {
  const [x0, y0, x1, y1] = extent
  return L.latLngBounds(ll(x0, y0), ll(x1, y1))
}

export function centroid(coords: [number, number][]): [number, number] {
  const pts = coords.slice(0, -1)
  return [pts.reduce((s, p) => s + p[0], 0) / pts.length, pts.reduce((s, p) => s + p[1], 0) / pts.length]
}

export const MAP_KEY: InjectionKey<ShallowRef<L.Map | null>> = Symbol('plan-map')

export type Selection =
  | { kind: 'sensor'; id: string }
  | { kind: 'vehicle'; id: string }
  | { kind: 'building'; id: string }
  | null

export const GEOZONE_STYLE: Record<string, L.PathOptions> = {
  gate: { color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.08, dashArray: '6 4', weight: 1.5 },
  docks: { color: '#38bdf8', fillColor: '#38bdf8', fillOpacity: 0.08, dashArray: '6 4', weight: 1.5 },
  parking: { color: '#a3a3a3', fillColor: '#a3a3a3', fillOpacity: 0.06, dashArray: '6 4', weight: 1.5 },
  restricted: { color: '#f87171', fillColor: '#f87171', fillOpacity: 0.06, dashArray: '6 4', weight: 1.5 },
}

export const SENSOR_GLYPH: Record<string, string> = {
  climate: 'T',
  motion: 'M',
  access_control: 'S',
  anpr_camera: 'C',
  gnss: 'G',
}

/** Arrow pointing up (north); rotated by heading. */
export const VEHICLE_SVG: Record<string, string> = {
  truck: '<path d="M12 2 L19 20 L12 16 L5 20 Z"/>',
  loader: '<path d="M12 4 L18 19 L12 15 L6 19 Z"/>',
  car: '<path d="M12 3 L18 20 L12 17 L6 20 Z"/>',
}
