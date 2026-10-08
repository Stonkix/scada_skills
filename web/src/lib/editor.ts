/**
 * Plan editor model: pure functions over the layout GeoJSON (format of deploy/seed/layout.geojson).
 * The view keeps a working copy, edits it through these helpers and saves it with POST /layout.
 */
import type { ObjectsResponse, SensorTypeInfo } from '@/api/types'

export type Pt = [number, number]

export interface Feature {
  type: 'Feature'
  id: string
  geometry: { type: 'Point'; coordinates: Pt } | { type: 'Polygon'; coordinates: Pt[][] } | { type: 'LineString'; coordinates: Pt[] }
  properties: Record<string, unknown> & { id: string; kind: string; name: string }
}

export interface Layout {
  type: 'FeatureCollection'
  metadata: { units: 'm'; extent: [number, number, number, number]; georef: { origin_lat: number; origin_lon: number; rotation_deg: number } }
  features: Feature[]
}

export const PLACEABLE = [
  { type: 'climate', prefix: 'clim', label: 'Климат', coverage: 15 },
  { type: 'motion', prefix: 'mot', label: 'Движение', coverage: 12 },
  { type: 'access_control', prefix: 'acs', label: 'СКУД', coverage: 3 },
  { type: 'anpr_camera', prefix: 'cam', label: 'Камера номеров', coverage: 25 },
] as const
export type PlaceableType = (typeof PLACEABLE)[number]['type']
export type Mode = 'select' | 'room' | PlaceableType

export const ROOM_TYPES: Record<string, string> = {
  storage: 'Склад / хранение',
  dock: 'Зона отгрузки',
  production: 'Производство',
  office: 'Офис',
  lobby: 'Вестибюль',
  server: 'Серверная',
  repair: 'Ремонт',
}

export function onEdge([x, y]: Pt, ring: Pt[]): boolean {
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (
      Math.abs((xj - xi) * (y - yi) - (yj - yi) * (x - xi)) < 1e-6 &&
      Math.min(xi, xj) - 1e-6 <= x && x <= Math.max(xi, xj) + 1e-6 &&
      Math.min(yi, yj) - 1e-6 <= y && y <= Math.max(yi, yj) + 1e-6
    ) return true
  }
  return false
}

/** Ray casting over the outer ring; points on the edge count as inside (sensors sit on walls). */
export function inPolygon(p: Pt, ring: Pt[]): boolean {
  if (onEdge(p, ring)) return true
  const [x, y] = p
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

const of = (l: Layout, kind: string) => l.features.filter((f) => f.properties.kind === kind)
const outer = (f: Feature) => (f.geometry as { coordinates: Pt[][] }).coordinates[0]

function area(ring: Pt[]): number {
  let a = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1])
  return Math.abs(a / 2)
}

/**
 * Where a point is: its building, and the most specific zone (room on that floor, else the building
 * itself for entrance readers, else the smallest outdoor geozone). Mirrors how the backend reasons.
 */
export function locate(l: Layout, p: Pt, floor: number): { building_id: string | null; zone_id: string | null; floor: number | null } {
  const building = of(l, 'building').find((b) => inPolygon(p, outer(b)))
  if (building) {
    const room = of(l, 'room')
      .filter((r) => r.properties.building_id === building.id && r.properties.floor === floor && inPolygon(p, outer(r)))
      .sort((a, b) => area(outer(a)) - area(outer(b)))[0]
    return { building_id: building.id, zone_id: room?.id ?? building.id, floor }
  }
  const zone = of(l, 'geozone')
    .filter((z) => z.properties.zone_type !== 'speed' && inPolygon(p, outer(z)))
    .sort((a, b) => area(outer(a)) - area(outer(b)))[0]
  return { building_id: null, zone_id: zone?.id ?? null, floor: null }
}

export const insideSite = (l: Layout, p: Pt) => {
  const [x0, y0, x1, y1] = l.metadata.extent
  return p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1
}

/** First free id like `clim-wh1-storage-2`, unique among plan features and registered sensors. */
export function nextId(base: string, taken: Set<string>): string {
  const clean = base.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  if (!taken.has(clean)) return clean
  for (let n = 2; ; n++) if (!taken.has(`${clean}-${n}`)) return `${clean}-${n}`
}

export function short(id: string | null): string {
  return id ? id.replace(/^(r|b|z)-/, '') : 'site'
}

export function newSensor(l: Layout, type: PlaceableType, p: Pt, floor: number, taken: Set<string>, typeName: string): Feature {
  const spec = PLACEABLE.find((s) => s.type === type)!
  const where = locate(l, p, floor)
  const id = nextId(`${spec.prefix}-${short(where.zone_id)}`, taken)
  const zoneName = l.features.find((f) => f.id === where.zone_id)?.properties.name
  return {
    type: 'Feature',
    id,
    geometry: { type: 'Point', coordinates: [round(p[0]), round(p[1])] },
    properties: { id, kind: 'sensor', name: `${typeName}: ${zoneName ?? 'территория'}`, sensor_type: type, coverage_m: spec.coverage, ...where },
  }
}

export function newRoom(l: Layout, a: Pt, b: Pt, floor: number, taken: Set<string>): Feature | string {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])].map(round)
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])].map(round)
  if (x1 - x0 < 3 || y1 - y0 < 3) return 'Помещение меньше 3×3 м'
  const corners: Pt[] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
  const building = of(l, 'building').find((bld) => corners.every((c) => inPolygon(c, outer(bld))))
  if (!building) return 'Помещение должно целиком лежать внутри здания'
  if (floor > Number(building.properties.floors ?? 1)) return `В здании ${building.properties.floors} эт.`
  const id = nextId(`r-${short(building.id)}-f${floor}-room`, taken)
  return {
    type: 'Feature',
    id,
    geometry: { type: 'Polygon', coordinates: [[...corners, corners[0]]] },
    properties: { id, kind: 'room', name: `${building.properties.name}, ${floor} эт.: новое помещение`, building_id: building.id, floor, room_type: 'storage' },
  }
}

/**
 * Keep sensor zones valid after rooms change, touching as little as possible: existing bindings were
 * chosen by people (an entrance reader on a wall belongs to the building, not to the room it touches).
 *  - a sensor whose zone disappeared is re-located;
 *  - a sensor strictly inside a newly drawn room (not on its walls) moves into that room.
 */
export function relocateSensors(l: Layout, addedRoom?: Feature): number {
  const zones = new Set(l.features.filter((f) => ['room', 'geozone', 'building'].includes(f.properties.kind)).map((f) => f.id))
  let changed = 0
  for (const s of of(l, 'sensor')) {
    const p = (s.geometry as { coordinates: Pt }).coordinates
    const floor = Number(s.properties.floor ?? 1)
    const zone = s.properties.zone_id as string | null
    if (zone && !zones.has(zone)) {
      Object.assign(s.properties, locate(l, p, floor))
      changed++
    } else if (
      addedRoom && s.properties.building_id === addedRoom.properties.building_id && floor === addedRoom.properties.floor &&
      inPolygon(p, outer(addedRoom)) && !onEdge(p, outer(addedRoom)) && zone !== addedRoom.id
    ) {
      s.properties.zone_id = addedRoom.id
      changed++
    }
  }
  return changed
}

export const round = (v: number) => Math.round(v * 10) / 10

/** Same checks the server makes, so problems show up before saving. */
export function validate(l: Layout): string[] {
  const errors: string[] = []
  const ids = new Map<string, number>()
  l.features.forEach((f) => ids.set(f.id, (ids.get(f.id) ?? 0) + 1))
  ids.forEach((n, id) => n > 1 && errors.push(`id ${id} встречается ${n} раза`))
  for (const s of of(l, 'sensor')) {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(s.id)) errors.push(`${s.id}: id — латиница, цифры, «-» и «_»`)
    if (!String(s.properties.name ?? '').trim()) errors.push(`${s.id}: пустое название`)
    if (!insideSite(l, (s.geometry as { coordinates: Pt }).coordinates)) errors.push(`${s.id}: за пределами площадки`)
  }
  return errors
}

/** What the save will do, for the confirmation line. */
export function diff(before: Layout, after: Layout) {
  const ids = (l: Layout, kind: string) => new Map(of(l, kind).map((f) => [f.id, JSON.stringify([f.geometry, f.properties])]))
  const count = (kind: string) => {
    const a = ids(before, kind)
    const b = ids(after, kind)
    return {
      added: [...b.keys()].filter((k) => !a.has(k)).length,
      removed: [...a.keys()].filter((k) => !b.has(k)).length,
      changed: [...b.keys()].filter((k) => a.has(k) && a.get(k) !== b.get(k)).length,
    }
  }
  return { sensors: count('sensor'), rooms: count('room') }
}

/** Shape the working layout like GET /objects so the shared plan renderer can draw it. */
export function toObjects(l: Layout, base: ObjectsResponse, sensorTypes: SensorTypeInfo[]): ObjectsResponse {
  const poly = (f: Feature) => ({ type: 'Polygon' as const, coordinates: (f.geometry as { coordinates: Pt[][] }).coordinates })
  const p = (f: Feature, k: string) => f.properties[k] as never
  return {
    ...base,
    extent: l.metadata.extent,
    site: of(l, 'site')[0] ? poly(of(l, 'site')[0]) : base.site,
    buildings: of(l, 'building').map((f) => ({ id: f.id, name: f.properties.name, building_type: p(f, 'building_type'), floors: p(f, 'floors'), geometry: poly(f) })),
    rooms: [], // the editor draws rooms itself (selectable)
    roads: of(l, 'road').map((f) => ({ id: f.id, name: f.properties.name, width_m: p(f, 'width_m'), speed_limit_kmh: p(f, 'speed_limit_kmh'),
      geometry: { type: 'LineString' as const, coordinates: (f.geometry as { coordinates: Pt[] }).coordinates } })),
    geozones: of(l, 'geozone').map((f) => ({ id: f.id, name: f.properties.name, zone_type: p(f, 'zone_type'), speed_limit_kmh: p(f, 'speed_limit_kmh') ?? null, geometry: poly(f) })),
    checkpoints: of(l, 'checkpoint').map((f) => {
      const [x, y] = (f.geometry as { coordinates: Pt }).coordinates
      return { id: f.id, name: f.properties.name, checkpoint_type: p(f, 'checkpoint_type'), zone_id: p(f, 'zone_id'), x, y }
    }),
    sensor_types: sensorTypes,
  }
}
