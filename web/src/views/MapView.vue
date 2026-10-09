<script setup lang="ts">
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibregl from 'maplibre-gl'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { Alert, ObjectsResponse, RoutePoint, SensorLive, Trip } from '@/api/types'
import AlertItem from '@/components/AlertItem.vue'
import BuildingPanel from '@/components/BuildingPanel.vue'
import CheatMenu from '@/components/CheatMenu.vue'
import MapSearch from '@/components/MapSearch.vue'
import ObjectCard from '@/components/ObjectCard.vue'
import SitePanel from '@/components/SitePanel.vue'
import TripBlock from '@/components/TripBlock.vue'
import TripsList from '@/components/TripsList.vue'
import { headline } from '@/lib/format'
import { lngLatRing, toLngLat } from '@/lib/geo'
import { GEOZONE_STYLE, type Selection } from '@/lib/plan'
import { BASEMAPS, SiteLayer, arrowIcon, baseStyle, metresToPixels, setBasemap, type Basemap } from '@/lib/realmap'
import { progress, slice, type Pt } from '@/lib/route'
import type { SearchItem } from '@/lib/search'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

const objects = useObjects()
const live = useLive()
const trips = useTrips()
const route = useRoute()
const router = useRouter()

const container = ref<HTMLDivElement>()
const map = shallowRef<maplibregl.Map | null>(null)
const selection = ref<Selection>(null)
const floor = ref(1)
const follow = ref(false)
const basemap = ref<Basemap>('scheme')
const tilted = ref(true)
const tab = ref<'alerts' | 'trips'>('alerts')
const showCheat = ref(false)
const routePoints = ref<RoutePoint[] | null>(null)
const trip = ref<Trip | null>(null)
const tooltip = ref<{ x: number; y: number; title: string; text: string } | null>(null)
let layer: SiteLayer | null = null
let resizer: ResizeObserver | null = null
let siteMarkers: { id: string; el: HTMLElement; marker: maplibregl.Marker }[] = []
let labels: { id: string; el: HTMLElement; marker: maplibregl.Marker }[] = []
let raf = 0
const unsubscribe = live.onSensorEvent(onSensorEvent)

const PITCH = 55
const SITE_LABEL_MAX_ZOOM = 14.5 // below: one marker per site; above: a label per building
const BUILDING_LABEL_MIN_ZOOM = 15.2
const TRAIL_MIN = 30 // minutes of live trail behind every vehicle

// --- helpers -------------------------------------------------------------------------------------
const g = () => objects.data!.georef
type FC = GeoJSON.FeatureCollection
const fc = (features: GeoJSON.Feature[]): FC => ({ type: 'FeatureCollection', features })
const line = (pts: Pt[], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: pts.map(([x, y]) => toLngLat(g(), x, y)) },
})
const polygon = (ring: [number, number][], props: Record<string, unknown> = {}): GeoJSON.Feature => ({
  type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [lngLatRing(g(), ring)] },
})
function boundsOf(ring: [number, number][]) {
  const xs = ring.map((p) => p[0])
  const ys = ring.map((p) => p[1])
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) }
}
function lngLatBounds(pts: [number, number][]) {
  const ll = pts.map(([x, y]) => toLngLat(g(), x, y))
  return ll.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(ll[0], ll[0]))
}
const siteBounds = computed(() =>
  new Map((objects.data?.sites ?? []).map((s) => [s.id, boundsOf(s.geometry.coordinates[0] as [number, number][])])),
)
function siteAt(x: number, y: number): string | null {
  for (const [id, b] of siteBounds.value) if (x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1) return id
  return null
}
const sensorStatus = (id: string) =>
  live.alertedObjects.get(id) === 'critical' ? 'critical' : live.sensors.get(id)?.status ?? 'unknown'
const RANK: Record<string, number> = { unknown: 0, ok: 1, offline: 2, warning: 3, critical: 4 }
function roomStatus(roomId: string) {
  let worst = 'unknown'
  for (const s of objects.data?.sensors ?? []) {
    if (s.zone_id === roomId && s.enabled && RANK[sensorStatus(s.id)] > RANK[worst]) worst = sensorStatus(s.id)
  }
  return worst
}

// --- what the selection means for the 3D scene ---------------------------------------------------
const inspected = computed(() => {
  const sel = selection.value
  const o = objects.data
  if (!sel || !o) return null
  if (sel.kind === 'site') return { siteId: sel.id, buildingId: null as string | null }
  if (sel.kind === 'building') return { siteId: objects.buildings.get(sel.id)?.site_id ?? null, buildingId: sel.id }
  if (sel.kind === 'sensor') {
    const s = objects.sensors.get(sel.id)
    if (!s || s.vehicle_id) return null
    if (s.building_id) return { siteId: objects.buildings.get(s.building_id)?.site_id ?? null, buildingId: s.building_id }
    return s.geo ? { siteId: siteAt(s.geo.x, s.geo.y), buildingId: null } : null
  }
  return null
})

function applyInspect() {
  const o = objects.data
  const ins = inspected.value
  if (!layer || !o) return
  if (!ins?.siteId) return layer.setInspect(null)
  const b = ins.buildingId ? objects.buildings.get(ins.buildingId) : undefined
  const outdoor = o.sensors.filter((s) => s.enabled && !s.building_id && !s.vehicle_id && s.geo && siteAt(s.geo.x, s.geo.y) === ins.siteId)
  const inside = b ? o.sensors.filter((s) => s.enabled && s.building_id === b.id && (s.floor ?? 1) === floor.value) : []
  layer.setInspect({ siteId: ins.siteId, building: b, floor: floor.value, sensors: [...inside, ...outdoor], rooms: o.rooms, sensorStatus, roomStatus })
}

watch(
  () => [inspected.value?.siteId, inspected.value?.buildingId, floor.value, objects.data?.layout_version] as const,
  applyInspect,
)

// --- static layers -------------------------------------------------------------------------------
function staticData(o: ObjectsResponse): Record<string, FC> {
  return {
    sites: fc(o.sites.map((s) => polygon(s.geometry.coordinates[0] as [number, number][], { id: s.id }))),
    roads: fc(o.roads.filter((r) => r.road_class !== 'public').map((r) => line(r.geometry.coordinates as Pt[], { id: r.id, width_m: r.width_m }))),
    routes: fc(o.roads.filter((r) => r.road_class === 'public').map((r) => line(r.geometry.coordinates as Pt[], { id: r.id }))),
    zones: fc(
      o.geozones
        .filter((z) => z.zone_type !== 'speed') // covers the whole site
        .map((z) => polygon(z.geometry.coordinates[0] as [number, number][], { id: z.id, color: GEOZONE_STYLE[z.zone_type]?.color ?? '#a3a3a3' })),
    ),
  }
}

function addLayers(m: maplibregl.Map, o: ObjectsResponse) {
  for (const [id, data] of Object.entries(staticData(o))) m.addSource(id, { type: 'geojson', data })
  for (const id of ['trails', 'history', 'plan', 'vehicles']) m.addSource(id, { type: 'geojson', data: fc([]) })
  const lat = o.georef.origin_lat
  const w = (z: number) => ['*', ['get', 'width_m'], metresToPixels(lat, z, 1)]
  m.addImage('arrow', arrowIcon(), { sdf: true })
  // the logistics network: highways between the sites, visible at every zoom
  m.addLayer({ id: 'routes-casing', type: 'line', source: 'routes', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#0b1120', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 3, 16, 8], 'line-opacity': 0.6 } })
  m.addLayer({ id: 'routes', type: 'line', source: 'routes', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#38bdf8', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.5, 16, 4], 'line-opacity': 0.55 } })
  // opaque yard surface: the site reads as our territory over whatever the basemap has there
  m.addLayer({ id: 'site-fill', type: 'fill', source: 'sites', paint: { 'fill-color': '#3a4150', 'fill-opacity': 0.92 } })
  m.addLayer({ id: 'site-line', type: 'line', source: 'sites', paint: { 'line-color': '#38bdf8', 'line-width': 2, 'line-dasharray': [3, 2] } })
  m.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', minzoom: 15, paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.14 } })
  m.addLayer({ id: 'zones-line', type: 'line', source: 'zones', minzoom: 15,
    paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-dasharray': [3, 2] } })
  m.addLayer({ id: 'roads', type: 'line', source: 'roads', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#1c222d', 'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, w(12), 22, w(22)] as never } })
  m.addLayer({ id: 'roads-axis', type: 'line', source: 'roads', minzoom: 16,
    paint: { 'line-color': '#e2e8f0', 'line-opacity': 0.5, 'line-width': 1, 'line-dasharray': [4, 4] } })
  // GLONASS-style tracks: a fading tail behind every vehicle, the selected one's real track and route ahead
  m.addLayer({ id: 'trails', type: 'line', source: 'trails', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': 2.5, 'line-opacity': 0.55 } })
  m.addLayer({ id: 'plan', type: 'line', source: 'plan', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#7dd3fc', 'line-width': 4, 'line-dasharray': [1.5, 1.5], 'line-opacity': 0.95 } })
  m.addLayer({ id: 'history', type: 'line', source: 'history', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#f97316', 'line-width': 4, 'line-opacity': 0.9 } })
  // zoomed out, 3D vehicles are specks: arrows instead
  m.addLayer({ id: 'vehicles', type: 'symbol', source: 'vehicles', maxzoom: 14,
    layout: { 'icon-image': 'arrow', 'icon-rotate': ['get', 'heading'], 'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true, 'icon-size': ['case', ['get', 'selected'], 0.75, 0.5] },
    paint: { 'icon-color': ['get', 'color'], 'icon-halo-color': '#0b1120', 'icon-halo-width': 2 } })
}

function addMarkers(m: maplibregl.Map, o: ObjectsResponse) {
  siteMarkers.forEach((s) => s.marker.remove())
  labels.forEach((l) => l.marker.remove())
  siteMarkers = o.sites.map((s) => {
    const el = document.createElement('button')
    el.className = 'site-marker'
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      select({ kind: 'site', id: s.id })
    })
    const b = siteBounds.value.get(s.id)!
    const marker = new maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat(toLngLat(g(), (b.x0 + b.x1) / 2, b.y1)).addTo(m)
    return { id: s.id, el, marker }
  })
  labels = o.buildings.map((b) => {
    const el = document.createElement('button')
    el.className = 'bld-label'
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      select({ kind: 'building', id: b.id })
    })
    const bb = boundsOf(b.geometry.coordinates[0] as [number, number][])
    // on the south wall: the building itself stays visible behind its label
    const marker = new maplibregl.Marker({ element: el, anchor: 'top' }).setLngLat(toLngLat(g(), (bb.x0 + bb.x1) / 2, bb.y0)).addTo(m)
    return { id: b.id, el, marker }
  })
  refreshMarkers()
  zoomMarkers()
}

function refreshMarkers() {
  const o = objects.data
  if (!o) return
  for (const s of siteMarkers) {
    const site = o.sites.find((x) => x.id === s.id)
    if (!site) continue
    const bids = new Set(o.buildings.filter((b) => b.site_id === s.id).map((b) => b.id))
    const sev = [...bids].map((id) => live.alertedObjects.get(id)).find((x) => x === 'critical') ?? [...bids].map((id) => live.alertedObjects.get(id)).find(Boolean)
    const here = [...live.vehicles.values()].filter((v) => siteAt(v.geo.x, v.geo.y) === s.id).length
    const coming = trips.enRoute.filter((t) => t.destination_site_id === s.id).length
    s.el.classList.toggle('sev-critical', sev === 'critical')
    s.el.classList.toggle('sev-warning', sev === 'warning')
    s.el.classList.toggle('on', selection.value?.kind === 'site' && selection.value.id === s.id)
    s.el.innerHTML = ''
    const name = document.createElement('b')
    name.textContent = site.name
    const meta = document.createElement('span')
    meta.textContent = `🚚 ${here} на месте${coming ? ` · ${coming} в пути сюда` : ''}`
    s.el.append(name, meta)
  }
  for (const l of labels) {
    const b = objects.buildings.get(l.id)
    if (!b) continue
    const sev = live.alertedObjects.get(l.id)
    l.el.classList.toggle('sev-warning', sev === 'warning')
    l.el.classList.toggle('sev-critical', sev === 'critical')
    l.el.classList.toggle('on', inspected.value?.buildingId === l.id)
    l.el.textContent = b.name
  }
}

function zoomMarkers() {
  const z = map.value?.getZoom() ?? 0
  for (const s of siteMarkers) s.el.style.display = z < SITE_LABEL_MAX_ZOOM ? '' : 'none'
  for (const l of labels) l.el.style.display = z >= BUILDING_LABEL_MIN_ZOOM ? '' : 'none'
}

/** A sensor reported: its pin flashes; with pins hidden, its building's label (or site marker) blinks. */
function onSensorEvent(s: SensorLive) {
  layer?.flash(s.sensor_id)
  const sensor = objects.sensors.get(s.sensor_id)
  if (!sensor || sensor.vehicle_id) return
  const el = sensor.building_id
    ? labels.find((l) => l.id === sensor.building_id)?.el
    : sensor.geo ? siteMarkers.find((m) => m.id === siteAt(sensor.geo!.x, sensor.geo!.y))?.el : undefined
  if (!el) return
  el.classList.remove('blink')
  void el.offsetWidth // restart the animation
  el.classList.add('blink')
}

// --- live: vehicles, trails, follow --------------------------------------------------------------
const trails = new Map<string, { t: number; p: Pt }[]>()
function recordTrails() {
  const now = Date.now()
  for (const v of live.vehicles.values()) {
    const tr = trails.get(v.vehicle_id) ?? []
    const last = tr[tr.length - 1]
    if (!last || Math.hypot(last.p[0] - v.geo.x, last.p[1] - v.geo.y) > 15) tr.push({ t: now, p: [v.geo.x, v.geo.y] })
    while (tr.length && now - tr[0].t > TRAIL_MIN * 60e3) tr.shift()
    trails.set(v.vehicle_id, tr)
  }
}

const VEHICLE_COLOR: Record<string, string> = { moving: '#fbbf24', idle: '#38bdf8', stopped: '#94a3b8', offline: '#475569' }
let lastOverlay = 0
function frame() {
  raf = requestAnimationFrame(frame)
  const m = map.value
  if (!m || !layer) return
  const sel = selection.value
  if (follow.value && sel?.kind === 'vehicle') {
    const p = layer.vehiclePosition(sel.id)
    if (p) m.jumpTo({ center: toLngLat(g(), ...p) })
  }
  const now = performance.now()
  if (now - lastOverlay < 120) return
  lastOverlay = now
  const vehicles = layer.vehicles().map((v) => ({
    type: 'Feature' as const,
    properties: {
      id: v.id, heading: v.heading, selected: sel?.kind === 'vehicle' && sel.id === v.id,
      color: live.alertedObjects.has(v.id) ? '#ef4444' : VEHICLE_COLOR[v.status],
    },
    geometry: { type: 'Point' as const, coordinates: toLngLat(g(), v.x, v.y) },
  }))
  ;(m.getSource('vehicles') as maplibregl.GeoJSONSource | undefined)?.setData(fc(vehicles))
}

function drawTrails() {
  const m = map.value
  if (!m?.getSource('trails')) return
  const features = [...trails.entries()]
    .filter(([, tr]) => tr.length > 1)
    .map(([id, tr]) => line(tr.map((x) => x.p), { id, color: VEHICLE_COLOR[live.vehicles.get(id)?.status ?? 'moving'] }))
  ;(m.getSource('trails') as maplibregl.GeoJSONSource).setData(fc(features))
}
const trailTimer = window.setInterval(drawTrails, 2000)

/** Selected vehicle: its real track for the last hour and what is left of its route. */
async function loadHistory(vehicleId: string) {
  const to = new Date()
  const from = new Date(to.getTime() - 60 * 60e3)
  const r = await api.GET('/vehicles/{vehicle_id}/route', {
    params: { path: { vehicle_id: vehicleId }, query: { from: from.toISOString(), to: to.toISOString() } },
  })
  if (selection.value?.kind === 'vehicle' && selection.value.id === vehicleId) routePoints.value = r.data?.points ?? null
}
watch(routePoints, (pts) => {
  const src = map.value?.getSource('history') as maplibregl.GeoJSONSource | undefined
  src?.setData(fc(pts && pts.length > 1 ? [line(pts.map((p) => [p.x, p.y] as Pt))] : []))
})
function drawPlan() {
  const src = map.value?.getSource('plan') as maplibregl.GeoJSONSource | undefined
  const t = trip.value
  const sel = selection.value
  const r = t && objects.data?.roads.find((x) => x.id === t.route_id)
  const v = sel?.kind === 'vehicle' ? live.vehicles.get(sel.id) : undefined
  if (!src) return
  if (!r || !v || !t || t.status === 'done' || t.status === 'unloading') return src.setData(fc([]))
  const pts = r.geometry.coordinates as Pt[]
  const reversed = r.connects?.[0] !== t.origin_site_id
  const directed = reversed ? [...pts].reverse() : pts
  const at = progress(pts, [v.geo.x, v.geo.y], reversed)
  src.setData(fc([line(t.status === 'en_route' ? slice(directed, at.done) : directed)]))
}
watch(() => [trip.value, live.vehicles] as const, drawPlan)

// --- map lifecycle -------------------------------------------------------------------------------
function fitRegion(animate = false) {
  const o = objects.data
  if (!o || !map.value) return
  const pts = o.sites.flatMap((s) => s.geometry.coordinates[0] as [number, number][])
  // fit flat, then tilt: fitBounds with a pitch zooms out far more than needed
  const cam = map.value.cameraForBounds(lngLatBounds(pts), { padding: { top: 90, bottom: 40, left: 60, right: 60 }, bearing: 0 })
  if (!cam) return
  const opts = { ...cam, pitch: tilted.value ? 35 : 0, bearing: 0 }
  if (animate) map.value.easeTo({ ...opts, duration: 900 })
  else map.value.jumpTo(opts)
}

function init(o: ObjectsResponse) {
  const m = new maplibregl.Map({
    container: container.value!,
    style: baseStyle(),
    center: toLngLat(o.georef, 0, 0),
    zoom: 10,
    maxPitch: 75,
    attributionControl: { compact: true },
  })
  // the grid cell settles after the map is created: keep the region framed until the user moves the camera
  let ready = false
  let userMoved = false
  resizer = new ResizeObserver(() => {
    m.resize()
    if (ready && !userMoved && !selection.value) fitRegion()
  })
  resizer.observe(container.value!)
  m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
  m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
  map.value = m
  if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = m // console debugging
  // not 'load': that waits for the first basemap tiles, which can take seconds; our layers need only the style
  m.once('style.load', () => {
    addLayers(m, o)
    layer = new SiteLayer(o.georef)
    m.addLayer(layer, 'trails') // under the tracks and arrows
    layer.setPlan(o)
    layer.updateVehicles(live.vehicles.values(), objects.vehicles)
    layer.setAlerted(live.alertedObjects)
    addMarkers(m, o)
    recordTrails()
    fitRegion()
    ready = true
    raf = requestAnimationFrame(frame)
    handleDeepLinks()
  })
  m.on('zoom', zoomMarkers)
  m.on('dragstart', (e) => {
    if (e.originalEvent) follow.value = false // the user took the camera
  })
  m.on('movestart', (e) => {
    if ((e as { originalEvent?: Event }).originalEvent) userMoved = true
  })
  m.on('click', (e) => {
    const hit = layer?.pick(e.point.x, e.point.y)
    if (hit) return select(hit)
    const v = m.queryRenderedFeatures(e.point, { layers: ['vehicles'] })[0]
    if (v) return select({ kind: 'vehicle', id: String(v.properties.id) })
    select(null) // empty space: drop the selection, the focus and the sensors
  })
  let hoverPending = false
  m.on('mousemove', (e) => {
    if (hoverPending) return
    hoverPending = true
    requestAnimationFrame(() => {
      hoverPending = false
      const hit = layer?.pick(e.point.x, e.point.y) ?? null
      const v = !hit && m.queryRenderedFeatures(e.point, { layers: ['vehicles'] }).length > 0
      m.getCanvas().style.cursor = hit || v ? 'pointer' : ''
      if (hit?.kind === 'sensor') {
        const s = objects.sensors.get(hit.id)
        tooltip.value = { x: e.point.x, y: e.point.y, title: s?.name ?? hit.id, text: headlineOf(hit.id) }
      } else if (hit?.kind === 'vehicle') {
        const reg = objects.vehicles.get(hit.id)
        const t = trips.byVehicle.get(hit.id)
        tooltip.value = { x: e.point.x, y: e.point.y, title: reg?.plate ?? hit.id, text: t ? `${t.cargo} → ${siteName(t.destination_site_id)}` : reg?.model ?? '' }
      } else tooltip.value = null
    })
  })
  m.on('mouseout', () => (tooltip.value = null))
}

const headlineOf = (id: string) => {
  const s = objects.sensors.get(id)
  return s ? headline(s.type, live.sensors.get(id)?.values) : ''
}
const siteName = (id: string) => objects.data?.sites.find((s) => s.id === id)?.name ?? id

watch(
  () => [objects.data, container.value] as const,
  ([o, el]) => {
    if (o && el && !map.value) init(o)
  },
  { immediate: true },
)

// a new plan (editor save) -> rebuild everything static
watch(
  () => objects.data?.layout_version,
  (v, old) => {
    const m = map.value
    const o = objects.data
    if (!m || !o || old === undefined || v === old || !m.isStyleLoaded()) return
    for (const [id, data] of Object.entries(staticData(o))) (m.getSource(id) as maplibregl.GeoJSONSource)?.setData(data)
    layer?.setPlan(o)
    addMarkers(m, o)
    applyInspect()
  },
)

watch(
  () => live.vehicles,
  (v) => {
    layer?.updateVehicles(v.values(), objects.vehicles)
    recordTrails()
  },
)
watch(
  () => [live.alertedObjects, live.people, trips.active] as const,
  () => {
    layer?.setAlerted(live.alertedObjects)
    refreshMarkers()
  },
)
let recolor = 0
watch(
  () => live.sensors,
  () => {
    if (recolor) return
    recolor = window.setTimeout(() => {
      recolor = 0
      layer?.recolorPins(sensorStatus)
    }, 1000)
  },
)
watch(basemap, (b) => map.value?.isStyleLoaded() && setBasemap(map.value, b))
watch(selection, (sel) => {
  layer?.setSelected(sel && (sel.kind === 'vehicle' || sel.kind === 'building') ? sel : null)
  refreshMarkers()
  if (sel?.kind !== 'vehicle') {
    routePoints.value = null
    trip.value = null
    follow.value = false
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.clearInterval(trailTimer)
  window.clearTimeout(recolor)
  unsubscribe()
  resizer?.disconnect()
  siteMarkers.forEach((s) => s.marker.remove())
  labels.forEach((l) => l.marker.remove())
  map.value?.remove()
  map.value = null
  layer = null
})

// --- selection and camera ------------------------------------------------------------------------
function select(sel: Selection) {
  const prev = selection.value
  selection.value = sel
  const m = map.value
  if (!sel || !m || !objects.data) return
  if (sel.kind === 'site') {
    const ring = objects.data.sites.find((s) => s.id === sel.id)?.geometry.coordinates[0] as [number, number][] | undefined
    if (ring) m.fitBounds(lngLatBounds(ring), { padding: 60, pitch: tilted.value ? PITCH : 0, bearing: m.getBearing(), duration: 1200 })
  } else if (sel.kind === 'building') {
    const b = objects.buildings.get(sel.id)
    if (!b) return
    if (prev?.kind !== 'sensor' || objects.sensors.get(prev.id)?.building_id !== sel.id) floor.value = 1
    const bb = boundsOf(b.geometry.coordinates[0] as [number, number][])
    m.flyTo({ center: toLngLat(g(), (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2), zoom: Math.max(m.getZoom(), 17.4),
      pitch: tilted.value ? PITCH : 0, duration: 1000 })
  } else if (sel.kind === 'sensor') {
    const s = objects.sensors.get(sel.id)
    if (s?.vehicle_id) return select({ kind: 'vehicle', id: s.vehicle_id })
    if (s?.floor) floor.value = s.floor
    if (s?.geo) m.flyTo({ center: toLngLat(g(), s.geo.x, s.geo.y), zoom: Math.max(m.getZoom(), 17.8), duration: 900 })
    else if (s?.building_id) {
      const b = objects.buildings.get(s.building_id)
      const bb = b && boundsOf(b.geometry.coordinates[0] as [number, number][])
      if (bb) m.flyTo({ center: toLngLat(g(), (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2), zoom: Math.max(m.getZoom(), 17.4), duration: 900 })
    }
  } else if (sel.kind === 'vehicle') {
    const p = layer?.vehiclePosition(sel.id)
    follow.value = false
    void loadHistory(sel.id)
    if (!p) return
    m.flyTo({ center: toLngLat(g(), ...p), zoom: Math.max(m.getZoom(), 15.5), pitch: tilted.value ? PITCH : 0, duration: 1200 })
    m.once('moveend', () => {
      if (selection.value?.kind === 'vehicle' && selection.value.id === sel.id) follow.value = true
    })
  }
}

function pickSearch(item: SearchItem) {
  if (item.kind === 'room') {
    select({ kind: 'building', id: item.building_id! })
    floor.value = item.floor ?? 1
  } else select({ kind: item.kind, id: item.id })
}

function locate(a: Alert) {
  const sel: Selection = a.vehicle_id
    ? { kind: 'vehicle', id: a.vehicle_id }
    : a.sensor_id
      ? { kind: 'sensor', id: a.sensor_id }
      : a.building_id
        ? { kind: 'building', id: a.building_id }
        : null
  if (sel) select(sel)
}

function toggleTilt() {
  tilted.value = !tilted.value
  map.value?.easeTo({ pitch: tilted.value ? PITCH : 0, bearing: tilted.value ? map.value.getBearing() : 0, duration: 700 })
}

/** Deep links: /?sensor=clim-wh1-dock (connectors page), /?alert=123 (toasts, the journal). */
async function handleDeepLinks() {
  const { sensor, alert } = route.query
  if (typeof sensor === 'string' && objects.sensors.has(sensor)) select({ kind: 'sensor', id: sensor })
  if (alert) {
    let a = live.alerts.get(Number(alert))
    if (!a) a = (await api.GET('/alerts/{alert_id}', { params: { path: { alert_id: Number(alert) } } })).data
    if (a) locate(a)
  }
  if (sensor || alert) router.replace({ query: {} })
}
watch(() => route.query, () => map.value?.isStyleLoaded() && handleDeepLinks())

const buildingOfSelection = computed(() => inspected.value?.buildingId ?? null)
const stats = computed(() => ({
  enRoute: trips.enRoute.length,
  moving: [...live.vehicles.values()].filter((v) => v.status === 'moving').length,
  people: [...live.people.values()].reduce((s, n) => s + n, 0),
}))
</script>

<template>
  <div class="map-view">
    <div class="map-area">
      <div ref="container" class="map" />
      <div v-if="!objects.data" class="empty-state">Загрузка плана…</div>

      <div class="top">
        <MapSearch @pick="pickSearch" />
      </div>

      <div class="toolbar panel">
        <div class="stats">
          <div><b class="mono">{{ stats.enRoute }}</b><small>рейсов в пути</small></div>
          <div><b class="mono">{{ stats.moving }}</b><small>едут</small></div>
          <div><b class="mono">{{ stats.people }}</b><small>людей</small></div>
          <div :class="{ 'st-critical': live.criticalCount }"><b class="mono">{{ live.openAlerts.length }}</b><small>тревог</small></div>
        </div>
        <div class="row">
          <div class="seg">
            <button v-for="(label, key) in BASEMAPS" :key="key" class="small" :class="{ on: basemap === key }" @click="basemap = key">{{ label }}</button>
          </div>
          <button class="small" @click="toggleTilt">{{ tilted ? 'Сверху' : '3D' }}</button>
          <button class="small ghost" title="Весь регион" @click="(select(null), fitRegion(true))">⤢</button>
        </div>
      </div>

      <div v-if="tooltip" class="tooltip" :style="{ left: `${tooltip.x + 14}px`, top: `${tooltip.y + 14}px` }">
        <b>{{ tooltip.title }}</b><span>{{ tooltip.text }}</span>
      </div>

      <button v-if="selection?.kind === 'vehicle' && !follow" class="follow-btn" @click="(follow = true)">◎ Следить за машиной</button>
      <button class="cheat-btn" :class="{ on: showCheat }" @click="showCheat = !showCheat">▶ Сценарии</button>
      <CheatMenu v-if="showCheat" class="cheat-panel" @close="showCheat = false" />
    </div>

    <aside class="side">
      <SitePanel v-if="selection?.kind === 'site'" :site-id="selection.id" @close="select(null)" @select="select" />
      <BuildingPanel
        v-else-if="selection?.kind === 'building'"
        :building-id="selection.id"
        :floor="floor"
        @floor="(f) => (floor = f)"
        @close="select(null)"
        @select="select"
      />
      <template v-else-if="selection">
        <button v-if="selection.kind === 'sensor' && buildingOfSelection" class="ghost small back" @click="select({ kind: 'building', id: buildingOfSelection })">
          ← {{ objects.buildings.get(buildingOfSelection)?.name }}
        </button>
        <ObjectCard :selection="selection" @close="select(null)" @route="(p) => (routePoints = p)" @select="(s) => s && select(s)">
          <template v-if="selection.kind === 'vehicle'" #vehicle>
            <TripBlock :vehicle-id="selection.id" @trip="(t) => (trip = t)" @site="(id) => select({ kind: 'site', id })" />
          </template>
        </ObjectCard>
      </template>
      <template v-else>
        <div class="tabs">
          <button :class="{ on: tab === 'alerts' }" @click="tab = 'alerts'">Тревоги <span class="mono">{{ live.openAlerts.length }}</span></button>
          <button :class="{ on: tab === 'trips' }" @click="tab = 'trips'">Рейсы в пути <span class="mono">{{ trips.enRoute.length }}</span></button>
        </div>
        <template v-if="tab === 'alerts'">
          <div v-if="!live.openAlerts.length" class="empty-state">Тревог нет — всё в норме</div>
          <div class="feed">
            <AlertItem v-for="a in live.openAlerts" :key="a.id" :alert="a" compact @locate="locate" />
          </div>
        </template>
        <div v-else class="feed">
          <TripsList @select="select" />
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.map-view {
  height: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
}
.map-area {
  position: relative;
  min-height: 0;
}
.map {
  position: absolute;
  inset: 0;
  background: var(--map-bg);
}
.side {
  border-left: 1px solid var(--border);
  background: var(--panel);
  padding: 14px;
  overflow: auto;
  display: grid;
  gap: 10px;
  align-content: start;
}
.feed {
  display: grid;
  gap: 8px;
}
.top {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  width: min(460px, calc(100% - 120px));
  z-index: 3;
  display: flex;
  justify-content: center;
}
.toolbar {
  position: absolute;
  top: 64px;
  left: 12px;
  z-index: 2;
  padding: 10px 12px;
  display: grid;
  gap: 10px;
  background: #111a2cee;
}
.stats {
  display: flex;
  gap: 14px;
}
.stats div {
  display: grid;
  line-height: 1.1;
}
.stats b {
  font-size: 18px;
}
.stats small {
  color: var(--text-muted);
  font-size: 11px;
}
.seg {
  display: flex;
}
.seg button {
  border-radius: 0;
}
.seg button:first-child {
  border-radius: 6px 0 0 6px;
}
.seg button:last-child {
  border-radius: 0 6px 6px 0;
  margin-left: -1px;
}
.seg button.on {
  border-color: var(--accent);
  color: var(--accent);
  position: relative;
}
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
}
.tabs button {
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  color: var(--text-muted);
}
.tabs button.on {
  color: var(--text);
  border-bottom-color: var(--accent);
}
.back {
  justify-self: start;
}
.tooltip {
  position: absolute;
  z-index: 4;
  pointer-events: none;
  display: grid;
  gap: 2px;
  padding: 6px 9px;
  border-radius: 6px;
  background: #0b1120ee;
  border: 1px solid var(--border);
  font-size: 12px;
  max-width: 280px;
}
.tooltip span {
  color: var(--text-muted);
}
.follow-btn {
  position: absolute;
  bottom: 56px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
  border-color: var(--accent);
  color: var(--accent);
  background: #111a2cee;
}
.cheat-btn {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 2;
  background: #3b2a06;
  border-color: #a16207;
  color: #fde68a;
  font-weight: 600;
}
.cheat-btn.on {
  background: #a16207;
  color: #111;
}
.cheat-panel {
  position: absolute;
  left: 12px;
  bottom: 56px;
  width: min(360px, calc(100% - 24px));
  max-height: calc(100% - 200px);
  z-index: 3;
  box-shadow: 0 10px 30px #000a;
}
:deep(.site-marker) {
  display: grid;
  gap: 1px;
  padding: 5px 10px;
  border-radius: 8px;
  background: #0b1120e6;
  border: 1px solid var(--accent);
  color: var(--text);
  font-size: 12px;
  line-height: 1.25;
  white-space: nowrap;
  cursor: pointer;
  text-align: center;
}
:deep(.site-marker span) {
  color: var(--text-muted);
  font-size: 11px;
}
:deep(.site-marker.sev-warning) {
  border-color: var(--st-warning);
}
:deep(.site-marker.sev-critical) {
  border-color: var(--st-critical);
  box-shadow: 0 0 12px var(--st-critical);
}
:deep(.bld-label) {
  padding: 3px 7px;
  border-radius: 6px;
  background: #0b1120d9;
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}
:deep(.bld-label.on),
:deep(.site-marker.on) {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent);
}
:deep(.bld-label.sev-warning) {
  border-color: var(--st-warning);
}
:deep(.bld-label.sev-critical) {
  border-color: var(--st-critical);
  box-shadow: 0 0 10px var(--st-critical);
}
:deep(.blink) {
  animation: marker-blink 0.9s ease-out;
}
@keyframes marker-blink {
  0% {
    background: #38bdf8;
    color: #0b1120;
  }
  100% {
    background: #0b1120d9;
  }
}
:deep(.maplibregl-ctrl-attrib) {
  font-size: 10px;
}
@media (max-width: 900px) {
  .map-view {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 1fr 42%;
  }
  .side {
    border-left: none;
    border-top: 1px solid var(--border);
  }
  .top {
    left: 8px;
    right: 56px;
    transform: none;
    width: auto;
  }
  .toolbar {
    top: 58px;
    left: 8px;
    padding: 8px;
  }
  .stats {
    gap: 10px;
  }
  .stats b {
    font-size: 15px;
  }
}
</style>
