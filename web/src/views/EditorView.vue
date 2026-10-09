<script setup lang="ts">
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibregl from 'maplibre-gl'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { ApiError, api, errorText, unwrap } from '@/api/client'
import type { ObjectsResponse, Room, Sensor } from '@/api/types'
import {
  BUILDING_KINDS,
  PLACEABLE,
  ROOM_TYPES,
  ZONE_KINDS,
  boundsOf,
  buildingProblem,
  diff,
  dragCorner,
  inPolygon,
  locate,
  newBuilding,
  newRoom,
  newSensor,
  newZone,
  nextId,
  partsOf,
  rectOf,
  rectRing,
  relocateSensors,
  removeWithParts,
  round,
  short,
  siteAt,
  toObjects,
  translate,
  validate,
  type BuildingKind,
  type Feature,
  type Layout,
  type PlaceableType,
  type Pt,
  type Rect,
  type ZoneKind,
} from '@/lib/editor'
import { lngLatRing, toLngLat, toLocal } from '@/lib/geo'
import { FLOOR_H } from '@/lib/models3d'
import { GEOZONE_STYLE } from '@/lib/plan'
import { BASEMAPS, SiteLayer, baseStyle, metresToPixels, setBasemap, type Basemap } from '@/lib/realmap'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

/**
 * 3D plan editor, Sims-style build mode: pick an item in the catalogue, drag it out on the ground
 * (buildings, rooms, zones) or click to place it (sensors); select to move, resize by the corners,
 * Delete to remove. Buildings open "walls down" to draw rooms floor by floor. Saved as one plan
 * version with POST /layout; sensors can also be registered in a building without a place.
 */
const objects = useObjects()
const toasts = useToasts()
const route = useRoute()

type Tool =
  | { kind: 'select' }
  | { kind: 'building'; type: BuildingKind }
  | { kind: 'room'; type: string }
  | { kind: 'zone'; type: ZoneKind }
  | { kind: 'sensor'; type: PlaceableType }
  | { kind: 'place'; id: string }

const working = ref<Layout | null>(null)
const original = ref('')
const version = ref(0)
const undoStack = ref<string[]>([])
const redoStack = ref<string[]>([])
const tool = ref<Tool>({ kind: 'select' })
const selectedId = ref<string | null>(null)
const floor = ref(1)
const siteId = ref<string>('')
const basemap = ref<Basemap>('scheme')
const saving = ref(false)
const problems = ref<string[]>([])
const conflict = ref(false)
const loadError = ref('')
const cursor = ref<{ x: number; y: number; text: string; bad: boolean } | null>(null)
const newSensorForm = ref<{ type: PlaceableType; name: string } | null>(null)
const container = ref<HTMLDivElement>()
const map = shallowRef<maplibregl.Map | null>(null)
let layer: SiteLayer | null = null
let resizer: ResizeObserver | null = null
let ready = false // our sources and layers are on the map
let userMoved = false

const taken = () => new Set([...(working.value?.features.map((f) => f.id) ?? []), ...objects.sensors.keys()])
const byId = (id: string | null) => working.value?.features.find((f) => f.id === id) ?? null
const outer = (f: Feature) => (f.geometry as { coordinates: Pt[][] }).coordinates[0]
const selected = computed(() => byId(selectedId.value))
const dirty = computed(() => !!working.value && JSON.stringify(working.value) !== original.value)
const changes = computed(() => (working.value && original.value ? diff(JSON.parse(original.value), working.value) : null))
const sites = computed(() => (working.value?.features ?? []).filter((f) => f.properties.kind === 'site'))
/** The building whose walls are down: the selected one, or the one the selected room / sensor is in. */
const openBuilding = computed(() => {
  const f = selected.value
  if (!f) return null
  if (f.properties.kind === 'building') return f
  return byId((f.properties.building_id as string) ?? null)
})
const floors = computed(() => Number(openBuilding.value?.properties.floors ?? 1))
/** Sensors registered in the open building that have no place on the plan yet. */
const unplaced = computed(() => {
  const b = openBuilding.value
  if (!b || !working.value) return []
  const onPlan = new Set(working.value.features.filter((f) => f.properties.kind === 'sensor').map((f) => f.id))
  return (objects.data?.sensors ?? []).filter((s) => s.building_id === b.id && s.enabled && !onPlan.has(s.id) && !s.vehicle_id)
})

// --- load / history / save -----------------------------------------------------------------------
async function load() {
  loadError.value = ''
  try {
    const doc = unwrap(await api.GET('/layout'))
    working.value = doc.geojson as unknown as Layout
    original.value = JSON.stringify(doc.geojson)
    version.value = doc.version
    undoStack.value = []
    redoStack.value = []
    conflict.value = false
    problems.value = []
  } catch (e) {
    loadError.value = errorText(e)
  }
}

function snapshot() {
  undoStack.value.push(JSON.stringify(working.value))
  if (undoStack.value.length > 100) undoStack.value.shift()
  redoStack.value = []
}
function undo() {
  const prev = undoStack.value.pop()
  if (!prev) return
  redoStack.value.push(JSON.stringify(working.value))
  working.value = JSON.parse(prev)
  if (!byId(selectedId.value)) selectedId.value = null
}
function redo() {
  const next = redoStack.value.pop()
  if (!next) return
  undoStack.value.push(JSON.stringify(working.value))
  working.value = JSON.parse(next)
}

async function save() {
  problems.value = validate(working.value!)
  if (problems.value.length) return
  saving.value = true
  try {
    const doc = unwrap(await api.POST('/layout', { body: { base_version: version.value, geojson: working.value as never } }))
    version.value = doc.version
    original.value = JSON.stringify(doc.geojson)
    working.value = doc.geojson as unknown as Layout
    await objects.load(true)
    toasts.push({ kind: 'info', title: `План сохранён (версия ${doc.version})`, text: 'Симулятор, коннекторы и worker подхватят изменения в течение минуты' })
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) conflict.value = true
    else problems.value = [errorText(e)]
  } finally {
    saving.value = false
  }
}

// --- the scene: layout -> renderer ---------------------------------------------------------------
const SENSOR_COLOR: Record<string, string> = { climate: '#22d3ee', motion: '#a78bfa', access_control: '#4ade80', anpr_camera: '#fbbf24', smoke: '#f97316' }
const ROOM_COLOR: Record<string, string> = { storage: '#64748b', dock: '#0ea5e9', production: '#f97316', office: '#a3e635', lobby: '#facc15', server: '#e879f9', repair: '#f87171' }

function asObjects(l: Layout): ObjectsResponse {
  const o = toObjects(l, objects.data!, objects.data!.sensor_types)
  o.buildings = o.buildings.map((b) => ({ ...b, site_id: siteAt(l, (b.geometry.coordinates[0] as Pt[])[0])?.id ?? null }))
  return o
}
const asRooms = (l: Layout): Room[] =>
  l.features.filter((f) => f.properties.kind === 'room').map((f) => ({
    id: f.id, name: f.properties.name, building_id: f.properties.building_id as string, floor: Number(f.properties.floor),
    room_type: String(f.properties.room_type), geometry: { type: 'Polygon', coordinates: (f.geometry as { coordinates: Pt[][] }).coordinates },
  }))
const asSensor = (f: Feature): Sensor => ({
  id: f.id, name: f.properties.name, type: f.properties.sensor_type as Sensor['type'], is_mobile: false, enabled: true,
  building_id: (f.properties.building_id as string) ?? null, zone_id: (f.properties.zone_id as string) ?? null,
  floor: (f.properties.floor as number) ?? null, geo: { x: (f.geometry.coordinates as Pt)[0], y: (f.geometry.coordinates as Pt)[1], floor: null },
  thresholds: [], created_at: '', description: null, vehicle_id: null,
})

let frame = 0
function redraw() {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    drawNow()
  })
}
function drawNow() {
  const m = map.value
  const l = working.value
  if (!m || !l || !layer || !objects.data || !ready) return
  const o = asObjects(l)
  layer.setPlan(o)
  const site = siteId.value
  const b = openBuilding.value
  const sensors = l.features.filter((f) => f.properties.kind === 'sensor')
  const outdoor = sensors.filter((f) => !f.properties.building_id && siteAt(l, f.geometry.coordinates as Pt)?.id === site)
  const inside = b ? sensors.filter((f) => f.properties.building_id === b.id && Number(f.properties.floor ?? 1) === floor.value) : []
  const typeOf = new Map(sensors.map((f) => [f.id, String(f.properties.sensor_type)]))
  const roomTypes = new Map(asRooms(l).map((r) => [r.id, r.room_type]))
  layer.setInspect({
    siteId: site, building: b ? o.buildings.find((x) => x.id === b.id) : undefined, floor: floor.value,
    sensors: [...inside, ...outdoor].map(asSensor), rooms: asRooms(l), sensorStatus: () => 'ok', roomStatus: () => 'ok',
    sensorColor: (id) => SENSOR_COLOR[typeOf.get(id) ?? ''] ?? '#64748b', roomColor: (id) => ROOM_COLOR[roomTypes.get(id) ?? ''] ?? '#475569',
  })
  layer.setSelected(selected.value?.properties.kind === 'building' ? { kind: 'building', id: selected.value.id } : null)
  setData('sites', sites.value.map((s) => poly(outer(s), { id: s.id })))
  setData('zones', l.features.filter((f) => f.properties.kind === 'geozone' && f.properties.zone_type !== 'speed')
    .map((z) => poly(outer(z), { id: z.id, color: GEOZONE_STYLE[String(z.properties.zone_type)]?.color ?? '#a3a3a3' })))
  setData('roads', l.features.filter((f) => f.properties.kind === 'road' && f.properties.road_class !== 'public')
    .map((r) => lineF(r.geometry.coordinates as Pt[], { width_m: r.properties.width_m })))
  drawSelection()
}

// --- 2D overlays (MapLibre) ----------------------------------------------------------------------
const g = () => objects.data!.georef
const poly = (ring: Pt[], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [lngLatRing(g(), ring)] },
})
const lineF = (pts: Pt[], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: pts.map(([x, y]) => toLngLat(g(), x, y)) },
})
const pointF = (p: Pt, props: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: toLngLat(g(), ...p) },
})
function setData(id: string, features: GeoJSON.Feature[]) {
  ;(map.value?.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features })
}

function drawSelection() {
  const f = selected.value
  if (!f) {
    setData('selection', [])
    setData('handles', [])
    return
  }
  if (f.geometry.type === 'Point') {
    setData('selection', [])
    setData('handles', [pointF(f.geometry.coordinates, { corner: -1 })])
    return
  }
  const ring = outer(f)
  setData('selection', [lineF(ring)])
  const r = boundsOf(ring)
  const corners: Pt[] = [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]]
  setData('handles', f.properties.kind === 'site' ? [] : corners.map((c, i) => pointF(c, { corner: i })))
}

/** Grid on the current site: the Sims floor, every 5 m. */
function drawGrid() {
  const s = sites.value.find((x) => x.id === siteId.value)
  if (!s) return setData('grid', [])
  const [x0, y0, x1, y1] = boundsOf(outer(s))
  const lines: GeoJSON.Feature[] = []
  for (let x = Math.ceil(x0 / 5) * 5; x <= x1; x += 5) lines.push(lineF([[x, y0], [x, y1]], { major: x % 25 === 0 }))
  for (let y = Math.ceil(y0 / 5) * 5; y <= y1; y += 5) lines.push(lineF([[x0, y], [x1, y]], { major: y % 25 === 0 }))
  setData('grid', lines)
}

function ghost(r: Rect | null, height = 0, bad = false) {
  setData('ghost', r ? [poly(rectRing(r), { h: height, color: bad ? '#ef4444' : '#38bdf8' })] : [])
}

function addLayers(m: maplibregl.Map) {
  for (const id of ['sites', 'grid', 'roads', 'zones', 'selection', 'handles', 'ghost']) {
    m.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  }
  const w = (z: number) => ['*', ['get', 'width_m'], metresToPixels(g().origin_lat, z, 1)]
  m.addLayer({ id: 'site-fill', type: 'fill', source: 'sites', paint: { 'fill-color': '#3a4150', 'fill-opacity': 0.92 } })
  m.addLayer({ id: 'grid', type: 'line', source: 'grid', minzoom: 15.5,
    paint: { 'line-color': '#94a3b8', 'line-opacity': ['case', ['get', 'major'], 0.28, 0.12], 'line-width': 1 } })
  m.addLayer({ id: 'site-line', type: 'line', source: 'sites', paint: { 'line-color': '#38bdf8', 'line-width': 2, 'line-dasharray': [3, 2] } })
  m.addLayer({ id: 'roads', type: 'line', source: 'roads', layout: { 'line-cap': 'round' },
    paint: { 'line-color': '#1c222d', 'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, w(12), 22, w(22)] as never } })
  m.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.18 } })
  m.addLayer({ id: 'zones-line', type: 'line', source: 'zones', paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-dasharray': [3, 2] } })
  layer = new SiteLayer(g())
  m.addLayer(layer)
  m.addLayer({ id: 'ghost', type: 'fill-extrusion', source: 'ghost',
    paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-opacity': 0.45 } })
  m.addLayer({ id: 'selection', type: 'line', source: 'selection', paint: { 'line-color': '#38bdf8', 'line-width': 3 } })
  m.addLayer({ id: 'handles', type: 'circle', source: 'handles',
    paint: { 'circle-radius': ['case', ['<', ['get', 'corner'], 0], 11, 7], 'circle-color': ['case', ['<', ['get', 'corner'], 0], 'rgba(0,0,0,0)', '#0b1120'],
      'circle-stroke-color': '#38bdf8', 'circle-stroke-width': 2.5 } })
}

// --- pointer interaction -------------------------------------------------------------------------
type Drag =
  | { mode: 'rect'; start: Pt; last: Pt }
  | { mode: 'move'; start: Pt; last: Pt; ids: string[]; before: string }
  | { mode: 'corner'; corner: number; before: string }
let drag: Drag | null = null

const ground = (pt: maplibregl.Point): Pt => {
  const ll = map.value!.unproject(pt)
  return toLocal(g(), ll.lng, ll.lat)
}

function pickAt(pt: maplibregl.Point): string | null {
  const hit = layer?.pick(pt.x, pt.y)
  if (hit) return hit.id
  const zone = map.value!.queryRenderedFeatures(pt, { layers: ['zones-fill'] })[0]
  return zone ? String(zone.properties.id) : null
}

const rectTool = computed(() => tool.value.kind === 'building' || tool.value.kind === 'room' || tool.value.kind === 'zone')

function ghostHeight(): number {
  const t = tool.value
  if (t.kind === 'building') return (BUILDING_KINDS.find((k) => k.type === t.type)!.floors) * FLOOR_H[t.type]
  return t.kind === 'room' ? 1.4 : 0.6
}

/** What dropping the rectangle would do: a new feature or why not. */
function tryRect(a: Pt, b: Pt): Feature | string {
  const l = working.value!
  const t = tool.value
  if (t.kind === 'building') return newBuilding(l, a, b, t.type, taken())
  if (t.kind === 'zone') return newZone(l, a, b, t.type, taken())
  if (t.kind === 'room') {
    const room = newRoom(l, a, b, floor.value, taken())
    if (typeof room !== 'string') {
      room.properties.room_type = t.type
      room.properties.name = `${openBuilding.value?.properties.name ?? ''}, ${floor.value} эт.: ${ROOM_TYPES[t.type].toLowerCase()}`
    }
    return room
  }
  return 'нет инструмента'
}

function onDown(e: maplibregl.MapMouseEvent) {
  if (e.originalEvent.button !== 0 || !working.value) return
  const p = ground(e.point)
  const t = tool.value
  if (rectTool.value) {
    drag = { mode: 'rect', start: p, last: p }
    map.value!.dragPan.disable()
    return
  }
  if (t.kind === 'sensor' || t.kind === 'place') return placeAt(p)
  const handle = map.value!.queryRenderedFeatures(e.point, { layers: ['handles'] })[0]
  if (handle && selected.value && Number(handle.properties.corner) >= 0) {
    drag = { mode: 'corner', corner: Number(handle.properties.corner), before: JSON.stringify(working.value) }
    map.value!.dragPan.disable()
    return
  }
  const id = pickAt(e.point)
  selectedId.value = id
  const f = byId(id)
  if (!f || f.properties.kind === 'site') return
  if (f.properties.kind === 'building') floor.value = Math.min(floor.value, Number(f.properties.floors ?? 1))
  if (f.properties.kind === 'sensor' && f.properties.floor) floor.value = Number(f.properties.floor)
  const ids = f.properties.kind === 'building' ? partsOf(working.value, f.id).map((x) => x.id) : [f.id]
  drag = { mode: 'move', start: p, last: p, ids, before: JSON.stringify(working.value) }
  map.value!.dragPan.disable()
}

function onMove(e: maplibregl.MapMouseEvent) {
  if (!working.value) return
  const p = ground(e.point)
  const at = { x: e.point.x, y: e.point.y }
  if (!drag) {
    if (rectTool.value) cursor.value = { ...at, text: hintFor(), bad: false }
    else if (tool.value.kind === 'sensor' || tool.value.kind === 'place') {
      const where = locate(working.value, p, floor.value)
      const name = where.zone_id ? byId(where.zone_id)?.properties.name ?? where.zone_id : siteAt(working.value, p) ? 'территория' : 'вне площадки'
      cursor.value = { ...at, text: `→ ${name}`, bad: !siteAt(working.value, p) }
    } else cursor.value = null
    return
  }
  if (drag.mode === 'rect') {
    drag.last = p
    const r = rectOf(drag.start, p)
    const res = tryRect(drag.start, p)
    const bad = typeof res === 'string'
    ghost(r, ghostHeight(), bad)
    cursor.value = { ...at, text: `${r[2] - r[0]} × ${r[3] - r[1]} м${bad ? ` — ${res}` : ''}`, bad }
  } else if (drag.mode === 'move') {
    const dx = Math.round(p[0] - drag.last[0])
    const dy = Math.round(p[1] - drag.last[1])
    if (!dx && !dy) return
    drag.last = [drag.last[0] + dx, drag.last[1] + dy]
    translate(working.value.features.filter((f) => (drag as { ids: string[] }).ids.includes(f.id)), dx, dy)
    const bad = moveProblem()
    cursor.value = bad ? { ...at, text: bad, bad: true } : null
    redraw()
  } else if (drag.mode === 'corner') {
    const f = selected.value!
    const before = JSON.parse(drag.before) as Layout
    const orig = before.features.find((x) => x.id === f.id)!
    const r = dragCorner(boundsOf(outer(orig)), drag.corner, p)
    ;(f.geometry as { coordinates: Pt[][] }).coordinates = [rectRing(r)]
    const bad = moveProblem()
    cursor.value = { ...at, text: `${r[2] - r[0]} × ${r[3] - r[1]} м${bad ? ` — ${bad}` : ''}`, bad: !!bad }
    redraw()
  }
}

/** After moving / resizing the selected feature: is it still valid where it is? */
function moveProblem(): string | null {
  const f = selected.value
  const l = working.value!
  if (!f) return null
  const kind = f.properties.kind
  if (kind === 'building') return buildingProblem(l, boundsOf(outer(f)), f.id)
  if (kind === 'room') {
    const b = byId(f.properties.building_id as string)
    return b && outer(f).every((p) => inPolygon(p, outer(b))) ? null : 'Помещение выходит за стены здания'
  }
  if (kind === 'geozone') return outer(f).every((p) => siteAt(l, p)) ? null : 'Зона выходит за площадку'
  if (kind === 'sensor') return siteAt(l, f.geometry.coordinates as Pt) ? null : 'Датчик вне площадки'
  return null
}

function onUp(e?: maplibregl.MapMouseEvent) {
  if (e && drag) onMove(e) // the release point counts even without a move event before it
  const d = drag
  drag = null
  map.value?.dragPan.enable()
  if (!d || !working.value) return
  if (d.mode === 'rect') {
    ghost(null)
    cursor.value = null
    if (Math.hypot(d.last[0] - d.start[0], d.last[1] - d.start[1]) < 1) return // a click, not a drag
    const res = tryRect(d.start, d.last)
    if (typeof res === 'string') return toasts.push({ kind: 'warning', title: 'Не получилось', text: res })
    snapshot()
    working.value.features.push(res)
    if (res.properties.kind === 'room') relocateSensors(working.value, res)
    selectedId.value = res.properties.kind === 'room' ? res.id : res.id
    if (res.properties.kind !== 'room') tool.value = { kind: 'select' }
  } else {
    const changed = JSON.stringify(working.value) !== d.before
    const bad = moveProblem()
    cursor.value = null
    if (bad) {
      working.value = JSON.parse(d.before)
      toasts.push({ kind: 'warning', title: 'Возвращено на место', text: bad })
    } else if (changed) {
      undoStack.value.push(d.before)
      redoStack.value = []
      const f = selected.value
      if (f?.properties.kind === 'sensor') Object.assign(f.properties, locate(working.value, f.geometry.coordinates as Pt, floor.value))
      relocateSensors(working.value)
    }
  }
  redraw()
}

function placeAt(p: Pt) {
  const l = working.value!
  if (!siteAt(l, p)) return toasts.push({ kind: 'warning', title: 'Не получилось', text: 'Датчик ставится на площадке' })
  const t = tool.value
  snapshot()
  if (t.kind === 'sensor') {
    const label = PLACEABLE.find((x) => x.type === t.type)!.label
    const f = newSensor(l, t.type, p, floor.value, taken(), label)
    l.features.push(f)
    selectedId.value = f.id
  } else if (t.kind === 'place') {
    const s = objects.sensors.get(t.id)!
    const where = locate(l, p, floor.value)
    l.features.push({
      type: 'Feature', id: s.id, geometry: { type: 'Point', coordinates: [round(p[0]), round(p[1])] },
      properties: { id: s.id, kind: 'sensor', name: s.name, sensor_type: s.type, ...where },
    })
    selectedId.value = s.id
    tool.value = { kind: 'select' } // one registered sensor, one place
  }
  redraw()
}

function remove() {
  const f = selected.value
  if (!f || !working.value || f.properties.kind === 'site') return
  if (f.properties.kind === 'building') {
    const n = partsOf(working.value, f.id).filter((x) => x.properties.kind === 'sensor').length
    if (!confirm(`Удалить «${f.properties.name}» вместе с помещениями${n ? ` и ${n} датчиками (они будут отключены)` : ''}?`)) return
  }
  snapshot()
  removeWithParts(working.value, f.id)
  selectedId.value = null
  redraw()
}

function setProp(key: string, value: unknown) {
  const f = selected.value
  if (!f) return
  snapshot()
  f.properties[key] = value
  if (key === 'floors' && f.properties.kind === 'building') floor.value = Math.min(floor.value, Number(value))
  redraw()
}

function hintFor(): string {
  const t = tool.value
  if (t.kind === 'building') return `Потяните по земле: ${BUILDING_KINDS.find((k) => k.type === t.type)!.label.toLowerCase()}`
  if (t.kind === 'zone') return 'Потяните по земле: зона'
  if (t.kind === 'room') return `Потяните внутри здания: ${ROOM_TYPES[t.type].toLowerCase()}`
  return ''
}

// --- registered sensors without a place: add one, or place one ----------------------------------
async function addUnplaced() {
  const b = openBuilding.value
  const form = newSensorForm.value
  if (!b || !form) return
  const spec = PLACEABLE.find((x) => x.type === form.type)!
  const id = nextId(`${spec.prefix}-${short(b.id)}`, taken())
  try {
    await api.POST('/sensors', {
      body: { id, type: form.type, name: form.name.trim() || `${spec.label}: ${b.properties.name}`, building_id: b.id, zone_id: b.id } as never,
    }).then(unwrap)
    await objects.load(true)
    newSensorForm.value = null
    toasts.push({ kind: 'info', title: 'Датчик добавлен', text: `${id} принимает данные; место на плане можно указать позже` })
  } catch (e) {
    toasts.push({ kind: 'warning', title: 'Не удалось добавить датчик', text: errorText(e) })
  }
}

// --- map lifecycle -------------------------------------------------------------------------------
function flyToSite(id: string, animate = true) {
  const s = sites.value.find((x) => x.id === id)
  const m = map.value
  if (!s || !m) return
  siteId.value = id
  const ring = outer(s).map(([x, y]) => toLngLat(g(), x, y))
  const b = ring.reduce((acc, p) => acc.extend(p), new maplibregl.LngLatBounds(ring[0], ring[0]))
  const cam = m.cameraForBounds(b, { padding: 40 })
  if (cam) m[animate ? 'easeTo' : 'jumpTo']({ ...cam, pitch: 50, bearing: -15, ...(animate ? { duration: 900 } : {}) })
  drawGrid()
}

function init() {
  const o = objects.data!
  const m = new maplibregl.Map({ container: container.value!, style: baseStyle(), center: toLngLat(o.georef, 0, 0), zoom: 16, maxPitch: 75,
    attributionControl: { compact: true } })
  // the grid cell settles after the map is created: keep the site framed until the user moves the camera
  resizer = new ResizeObserver(() => {
    m.resize()
    if (ready && !userMoved) flyToSite(siteId.value, false)
  })
  m.on('movestart', (e) => {
    if ((e as { originalEvent?: Event }).originalEvent) userMoved = true
  })
  resizer.observe(container.value!)
  m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
  m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
  map.value = m
  m.once('style.load', () => {
    addLayers(m)
    ready = true
    const want = typeof route.query.building === 'string' ? route.query.building : null
    const b = want ? byId(want) : null
    const start = (b && siteAt(working.value!, outer(b)[0])?.id) || sites.value[0]?.id || ''
    flyToSite(start, false)
    if (b) selectedId.value = b.id
    drawNow()
  })
  m.on('mousedown', onDown)
  m.on('mousemove', onMove)
  m.on('mouseup', onUp)
  m.on('mouseout', () => !drag && (cursor.value = null))
  window.addEventListener('mouseup', onWindowUp) // released outside the map
}
const onWindowUp = () => onUp()

watch(
  () => [working.value, objects.data, container.value] as const,
  ([l, o, el]) => {
    if (l && o && el && !map.value) init()
  },
)
watch(() => [working.value, selectedId.value, floor.value, siteId.value], redraw, { deep: true })
watch(basemap, (b) => ready && map.value && setBasemap(map.value, b))
watch(siteId, drawGrid)

function onKey(e: KeyboardEvent) {
  if ((e.target as HTMLElement).closest('input, select, textarea')) return
  const mod = e.ctrlKey || e.metaKey
  if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) undo()
  else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) redo()
  else if (e.key === 'Delete' || e.key === 'Backspace') remove()
  else if (e.key === 'Escape') {
    if (tool.value.kind !== 'select') tool.value = { kind: 'select' }
    else selectedId.value = null
  } else return
  e.preventDefault()
}

const beforeUnload = (e: BeforeUnloadEvent) => {
  if (dirty.value) e.preventDefault()
}
onMounted(() => {
  void load()
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('mouseup', onWindowUp)
  cancelAnimationFrame(frame)
  resizer?.disconnect()
  map.value?.remove()
  map.value = null
  layer = null
})
onBeforeRouteLeave(() => !dirty.value || confirm('В плане есть несохранённые изменения. Уйти без сохранения?'))

const isTool = (kind: Tool['kind'], type?: string) =>
  tool.value.kind === kind && (type === undefined || (tool.value as { type?: string; id?: string }).type === type || (tool.value as { id?: string }).id === type)
const pick = (t: Tool) => {
  tool.value = isTool(t.kind, (t as { type?: string; id?: string }).type ?? (t as { id?: string }).id) ? { kind: 'select' } : t
}
const ICON: Record<string, string> = {
  warehouse: '🏬', production: '🏭', office: '🏢', garage: '🔧',
  storage: '📦', dock: '🚚', lobby: '🛋️', server: '🖥️', repair: '🔩',
  climate: '🌡️', motion: '👁️', access_control: '🪪', anpr_camera: '📷', smoke: '🔥',
  gate: '🚧', docks: '📥', parking: '🅿️', restricted: '⛔',
}
const ROOM_ICON = (t: string) => (t === 'production' ? '⚙️' : t === 'office' ? '💼' : ICON[t] ?? '▫️')
const siteOfSelected = computed(() => (selected.value && working.value ? siteAt(working.value, (selected.value.geometry.type === 'Point' ? selected.value.geometry.coordinates : outer(selected.value)[0]) as Pt) : null))
const CHANGE_LABEL: Record<string, string> = { sensors: 'датчики', rooms: 'помещения', buildings: 'здания', zones: 'зоны' }
const sizeOf = (f: Feature) => {
  const r = boundsOf(outer(f))
  return `${r[2] - r[0]} × ${r[3] - r[1]} м`
}
</script>

<template>
  <div class="editor">
    <!-- catalogue -->
    <aside class="catalog">
      <section>
        <h3>Здания</h3>
        <div class="tiles">
          <button v-for="k in BUILDING_KINDS" :key="k.type" class="tile" :class="{ on: isTool('building', k.type) }"
                  @click="pick({ kind: 'building', type: k.type })">
            <span class="ico">{{ ICON[k.type] }}</span>{{ k.label }}
          </button>
        </div>
      </section>
      <section>
        <h3>Помещения <small v-if="!openBuilding" class="muted">— выберите здание</small></h3>
        <div class="tiles">
          <button v-for="(label, type) in ROOM_TYPES" :key="type" class="tile" :disabled="!openBuilding"
                  :class="{ on: isTool('room', type) }" @click="pick({ kind: 'room', type })">
            <span class="ico">{{ ROOM_ICON(type) }}</span>{{ label }}
          </button>
        </div>
      </section>
      <section>
        <h3>Датчики</h3>
        <div class="tiles">
          <button v-for="s in PLACEABLE" :key="s.type" class="tile" :class="{ on: isTool('sensor', s.type) }"
                  @click="pick({ kind: 'sensor', type: s.type })">
            <span class="ico" :style="{ color: SENSOR_COLOR[s.type] }">{{ ICON[s.type] }}</span>{{ s.label }}
          </button>
        </div>
      </section>
      <section>
        <h3>Зоны</h3>
        <div class="tiles">
          <button v-for="z in ZONE_KINDS" :key="z.type" class="tile" :class="{ on: isTool('zone', z.type) }"
                  @click="pick({ kind: 'zone', type: z.type })">
            <span class="ico">{{ ICON[z.type] }}</span>{{ z.label }}
          </button>
        </div>
      </section>
      <p class="muted keys">Esc — выбор · Del — удалить · Ctrl+Z / Ctrl+Y — отмена / повтор · тяните объект, чтобы переместить, углы — чтобы изменить размер</p>
    </aside>

    <div class="stage">
      <div ref="container" class="map" />
      <div v-if="loadError" class="empty-state">{{ loadError }}</div>
      <div class="bar panel">
        <select :value="siteId" aria-label="Площадка" @change="flyToSite(($event.target as HTMLSelectElement).value)">
          <option v-for="s in sites" :key="s.id" :value="s.id">{{ s.properties.name }}</option>
        </select>
        <div class="seg">
          <button v-for="(label, key) in BASEMAPS" :key="key" class="small" :class="{ on: basemap === key }" @click="basemap = key">{{ label }}</button>
        </div>
        <button class="small" :disabled="!undoStack.length" title="Ctrl+Z" @click="undo">↶</button>
        <button class="small" :disabled="!redoStack.length" title="Ctrl+Y" @click="redo">↷</button>
        <span v-if="changes && dirty" class="muted small">
          <template v-for="(c, key) in changes" :key="key">
            <span v-if="c.added || c.removed || c.changed" class="chg">
              {{ CHANGE_LABEL[key] }} <b v-if="c.added">+{{ c.added }}</b> <b v-if="c.removed">−{{ c.removed }}</b> <b v-if="c.changed">~{{ c.changed }}</b>
            </span>
          </template>
        </span>
        <button class="primary small" :disabled="!dirty || saving" @click="save">{{ saving ? 'Сохранение…' : 'Сохранить план' }}</button>
      </div>
      <div v-if="openBuilding && floors > 1" class="floors panel">
        <span class="muted">Этаж</span>
        <button v-for="f in floors" :key="f" class="small" :class="{ on: floor === f }" @click="floor = f">{{ f }}</button>
      </div>
      <div v-if="cursor" class="cursor" :class="{ bad: cursor.bad }" :style="{ left: `${cursor.x + 16}px`, top: `${cursor.y + 16}px` }">{{ cursor.text }}</div>
      <div v-if="conflict" class="banner">
        План успел сохранить кто-то другой. <button class="small" @click="load">Загрузить свежую версию</button> (ваши правки пропадут)
      </div>
      <div v-if="problems.length" class="banner bad">
        <b>Не сохранено:</b>
        <ul><li v-for="p in problems.slice(0, 6)" :key="p">{{ p }}</li></ul>
      </div>
    </div>

    <!-- properties -->
    <aside class="props">
      <template v-if="selected">
        <header class="row">
          <h2 class="grow">{{ selected.properties.name }}</h2>
          <button class="ghost small" aria-label="Снять выбор" @click="selectedId = null">✕</button>
        </header>
        <small class="muted mono">{{ selected.id }} · {{ siteOfSelected?.properties.name ?? 'вне площадок' }}</small>
        <label>Название <input :value="selected.properties.name" @change="setProp('name', ($event.target as HTMLInputElement).value)" /></label>

        <template v-if="selected.properties.kind === 'building'">
          <label>Тип
            <select :value="selected.properties.building_type" @change="setProp('building_type', ($event.target as HTMLSelectElement).value)">
              <option v-for="k in BUILDING_KINDS" :key="k.type" :value="k.type">{{ k.label }}</option>
            </select>
          </label>
          <label>Этажей <input type="number" min="1" max="20" :value="selected.properties.floors"
                               @change="setProp('floors', Math.max(1, Number(($event.target as HTMLInputElement).value)))" /></label>
          <div class="muted small">{{ sizeOf(selected) }} · помещений: {{ working!.features.filter((f) => f.properties.building_id === selected!.id && f.properties.kind === 'room').length }}</div>
        </template>
        <template v-else-if="selected.properties.kind === 'room'">
          <label>Назначение
            <select :value="selected.properties.room_type" @change="setProp('room_type', ($event.target as HTMLSelectElement).value)">
              <option v-for="(label, t) in ROOM_TYPES" :key="t" :value="t">{{ label }}</option>
            </select>
          </label>
          <div class="muted small">{{ sizeOf(selected) }} · {{ selected.properties.floor }} эт. · {{ byId(selected.properties.building_id as string)?.properties.name }}</div>
        </template>
        <template v-else-if="selected.properties.kind === 'geozone'">
          <label>Тип зоны
            <select :value="selected.properties.zone_type" @change="setProp('zone_type', ($event.target as HTMLSelectElement).value)">
              <option v-for="z in ZONE_KINDS" :key="z.type" :value="z.type">{{ z.label }}</option>
              <option value="speed">Ограничение скорости</option>
            </select>
          </label>
          <div class="muted small">{{ sizeOf(selected) }}</div>
        </template>
        <template v-else-if="selected.properties.kind === 'sensor'">
          <div class="muted small">
            {{ PLACEABLE.find((p) => p.type === selected!.properties.sensor_type)?.label ?? selected.properties.sensor_type }} ·
            {{ byId(selected.properties.zone_id as string)?.properties.name ?? 'территория' }}{{ selected.properties.floor ? ` · ${selected.properties.floor} эт.` : '' }}
          </div>
        </template>
        <button v-if="selected.properties.kind !== 'site'" class="danger small" @click="remove">Удалить</button>

        <!-- sensors of the open building without a place on the plan -->
        <section v-if="openBuilding" class="unplaced">
          <h3>Датчики без места на плане</h3>
          <p class="muted small">Датчик можно завести без размещения — он сразу работает, а место укажете позже.</p>
          <div v-for="s in unplaced" :key="s.id" class="row">
            <span class="grow small">{{ s.name }}</span>
            <button class="small" :class="{ on: isTool('place', s.id) }" @click="pick({ kind: 'place', id: s.id })">
              {{ isTool('place', s.id) ? 'кликните на плане…' : 'Разместить' }}
            </button>
          </div>
          <div v-if="!unplaced.length" class="muted small">нет</div>
          <div v-if="newSensorForm" class="form">
            <select v-model="newSensorForm.type">
              <option v-for="p in PLACEABLE" :key="p.type" :value="p.type">{{ p.label }}</option>
            </select>
            <input v-model="newSensorForm.name" placeholder="Название (необязательно)" />
            <div class="row">
              <button class="primary small" @click="addUnplaced">Добавить</button>
              <button class="ghost small" @click="newSensorForm = null">Отмена</button>
            </div>
          </div>
          <button v-else class="small" @click="newSensorForm = { type: 'climate', name: '' }">＋ Датчик без размещения</button>
        </section>
      </template>
      <template v-else>
        <h2>Редактор плана</h2>
        <p class="muted small">
          Выберите объект в каталоге слева и потяните по земле — как в Sims. Кликните по зданию, чтобы опустить стены
          и рисовать помещения по этажам. Изменения применяются одной версией плана по кнопке «Сохранить».
        </p>
        <h3>Здания площадки</h3>
        <button v-for="b in working?.features.filter((f) => f.properties.kind === 'building' && siteAt(working!, outer(f)[0])?.id === siteId) ?? []"
                :key="b.id" class="item" @click="selectedId = b.id">
          <span>{{ ICON[String(b.properties.building_type)] }}</span><span class="grow">{{ b.properties.name }}</span>
          <small class="muted">{{ sizeOf(b) }}</small>
        </button>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.editor {
  height: 100%;
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr) 320px;
}
.catalog,
.props {
  background: var(--panel);
  overflow: auto;
  padding: 12px;
  display: grid;
  gap: 12px;
  align-content: start;
}
.catalog {
  border-right: 1px solid var(--border);
}
.props {
  border-left: 1px solid var(--border);
}
h3 {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.tiles {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.tile {
  display: grid;
  justify-items: center;
  gap: 2px;
  padding: 8px 4px;
  font-size: 11px;
  white-space: normal;
  text-align: center;
  line-height: 1.2;
  border-radius: 8px;
}
.tile .ico {
  font-size: 22px;
}
.tile.on {
  border-color: var(--accent);
  background: #0c4a6e;
  box-shadow: 0 0 0 1px var(--accent);
}
.keys {
  font-size: 11px;
}
.chg + .chg::before {
  content: ' · ';
}
.stage {
  position: relative;
  min-height: 0;
}
.map {
  position: absolute;
  inset: 0;
  background: var(--map-bg);
}
.bar {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 60px;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #111a2cee;
}
.bar .primary {
  margin-left: auto;
}
.seg {
  display: flex;
}
.seg button {
  border-radius: 0;
}
.seg button.on,
.floors button.on,
button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.floors {
  position: absolute;
  top: 64px;
  left: 10px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #111a2cee;
}
.cursor {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  padding: 4px 8px;
  border-radius: 6px;
  background: #0b1120ee;
  border: 1px solid var(--accent);
  font-size: 12px;
  white-space: nowrap;
}
.cursor.bad {
  border-color: var(--st-critical);
  color: #fecaca;
}
.banner {
  position: absolute;
  left: 10px;
  bottom: 10px;
  z-index: 2;
  padding: 10px 12px;
  border-radius: 8px;
  background: #3b2a06ee;
  border: 1px solid #a16207;
  color: #fde68a;
  max-width: 520px;
}
.banner.bad {
  background: #3b1219ee;
  border-color: #7f1d1d;
  color: #fecaca;
}
.banner ul {
  margin: 4px 0 0;
  padding-left: 18px;
}
label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted);
}
.small {
  font-size: 12px;
}
.unplaced {
  display: grid;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.form {
  display: grid;
  gap: 6px;
}
.item {
  display: flex;
  gap: 8px;
  align-items: center;
  text-align: left;
  font-size: 12px;
  white-space: normal;
}
@media (max-width: 1100px) {
  .editor {
    grid-template-columns: 200px minmax(0, 1fr);
  }
  .props {
    grid-column: 1 / -1;
    border-left: none;
    border-top: 1px solid var(--border);
    max-height: 40vh;
  }
}
</style>
