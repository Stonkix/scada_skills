<script setup lang="ts">
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibregl from 'maplibre-gl'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import type { Alert, ObjectsResponse, RoutePoint } from '@/api/types'
import AlertItem from '@/components/AlertItem.vue'
import ObjectCard from '@/components/ObjectCard.vue'
import { lngLatRing, toLngLat, toLocal, type Georef } from '@/lib/geo'
import { GEOZONE_STYLE, type Selection } from '@/lib/plan'
import { BASEMAPS, SiteLayer, baseStyle, metresToPixels, setBasemap, type Basemap } from '@/lib/realmap'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const objects = useObjects()
const live = useLive()

const container = ref<HTMLDivElement>()
const map = shallowRef<maplibregl.Map | null>(null)
const selection = ref<Selection>(null)
const routePoints = ref<RoutePoint[] | null>(null)
const basemap = ref<Basemap>('scheme')
const tilted = ref(true)
let layer: SiteLayer | null = null
let labels: { el: HTMLElement; id: string; marker: maplibregl.Marker }[] = []
let resizer: ResizeObserver | null = null

const PITCH = 58
const BEARING = -28

const stats = computed(() => {
  const vehicles = [...live.vehicles.values()]
  return {
    moving: vehicles.filter((v) => v.status === 'moving').length,
    total: vehicles.length,
    people: [...live.people.values()].reduce((s, n) => s + n, 0),
  }
})

// --- GeoJSON for the flat layers (site, roads, geozones, outdoor sensors, route) -----------------
type FC = GeoJSON.FeatureCollection
const polygon = (g: Georef, coords: [number, number][], props: Record<string, unknown>): GeoJSON.Feature => ({
  type: 'Feature',
  properties: props,
  geometry: { type: 'Polygon', coordinates: [lngLatRing(g, coords)] },
})

function staticSources(o: ObjectsResponse): Record<string, FC> {
  const g = o.georef
  return {
    site: { type: 'FeatureCollection', features: [polygon(g, o.site.coordinates[0] as [number, number][], {})] },
    roads: {
      type: 'FeatureCollection',
      features: o.roads.map((r) => ({
        type: 'Feature',
        properties: { id: r.id, width_m: r.width_m },
        geometry: { type: 'LineString', coordinates: lngLatRing(g, r.geometry.coordinates as [number, number][]) },
      })),
    },
    zones: {
      type: 'FeatureCollection',
      features: o.geozones
        .filter((z) => z.zone_type !== 'speed') // covers the whole site
        .map((z) =>
          polygon(g, z.geometry.coordinates[0] as [number, number][], {
            id: z.id,
            color: GEOZONE_STYLE[z.zone_type]?.color ?? '#a3a3a3',
          }),
        ),
    },
  }
}

function sensorsFC(o: ObjectsResponse): FC {
  return {
    type: 'FeatureCollection',
    features: o.sensors
      .filter((s) => s.geo && !s.building_id && !s.vehicle_id && s.enabled !== false)
      .map((s) => {
        const st = live.sensors.get(s.id)?.status ?? 'unknown'
        const alert = live.alertedObjects.get(s.id)
        return {
          type: 'Feature',
          properties: { id: s.id, color: alert === 'critical' ? STATUS_HEX.critical : (STATUS_HEX[st] ?? STATUS_HEX.unknown) },
          geometry: { type: 'Point', coordinates: toLngLat(o.georef, s.geo!.x, s.geo!.y) },
        }
      }),
  }
}

const STATUS_HEX: Record<string, string> = {
  ok: '#22c55e',
  warning: '#f59e0b',
  critical: '#ef4444',
  offline: '#475569',
  unknown: '#64748b',
}

function routeFC(o: ObjectsResponse, pts: RoutePoint[] | null): FC {
  return {
    type: 'FeatureCollection',
    features:
      pts && pts.length > 1
        ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: pts.map((p) => toLngLat(o.georef, p.x, p.y)) } }]
        : [],
  }
}

function addFlatLayers(m: maplibregl.Map, o: ObjectsResponse) {
  const src = staticSources(o)
  for (const [id, data] of Object.entries(src)) m.addSource(id, { type: 'geojson', data })
  m.addSource('sensors', { type: 'geojson', data: sensorsFC(o) })
  m.addSource('route', { type: 'geojson', data: routeFC(o, null) })
  const lat = o.georef.origin_lat
  const w = (z: number) => ['*', ['get', 'width_m'], metresToPixels(lat, z, 1)]
  // opaque yard surface: the site reads as our territory over whatever the basemap has there
  m.addLayer({ id: 'site-fill', type: 'fill', source: 'site', paint: { 'fill-color': '#3a4150', 'fill-opacity': 0.92 } })
  m.addLayer({ id: 'site-line', type: 'line', source: 'site', paint: { 'line-color': '#38bdf8', 'line-width': 2, 'line-dasharray': [3, 2] } })
  m.addLayer({
    id: 'zones-fill',
    type: 'fill',
    source: 'zones',
    paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.14 },
  })
  m.addLayer({
    id: 'zones-line',
    type: 'line',
    source: 'zones',
    paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-dasharray': [3, 2] },
  })
  m.addLayer({
    id: 'roads',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    // widths are real metres at every zoom
    paint: {
      'line-color': '#1c222d',
      'line-width': ['interpolate', ['exponential', 2], ['zoom'], 12, w(12), 22, w(22)] as never,
    },
  })
  m.addLayer({
    id: 'roads-axis',
    type: 'line',
    source: 'roads',
    minzoom: 16,
    paint: { 'line-color': '#e2e8f0', 'line-opacity': 0.5, 'line-width': 1, 'line-dasharray': [4, 4] },
  })
  m.addLayer({
    id: 'route',
    type: 'line',
    source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#d95926', 'line-width': 4, 'line-opacity': 0.9 },
  })
  m.addLayer({
    id: 'sensors',
    type: 'circle',
    source: 'sensors',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 3, 18, 7],
      'circle-color': ['get', 'color'],
      'circle-stroke-color': '#0b1120',
      'circle-stroke-width': 1.5,
    },
  })
}

// --- building labels: name, people inside, alert --------------------------------------------------
function peopleIn(buildingId: string): number {
  const rooms = new Set((objects.data?.rooms ?? []).filter((r) => r.building_id === buildingId).map((r) => r.id))
  let n = 0
  for (const [zone, count] of live.people) if (zone === buildingId || rooms.has(zone)) n += count
  return n
}

function addLabels(m: maplibregl.Map, o: ObjectsResponse) {
  labels.forEach((l) => l.marker.remove())
  labels = o.buildings.map((b) => {
    const el = document.createElement('div')
    el.className = 'bld-label'
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      focus({ kind: 'building', id: b.id })
    })
    const ring = b.geometry.coordinates[0] as [number, number][]
    const xs = ring.map((p) => p[0])
    const ys = ring.map((p) => p[1])
    // the label stands on the south wall so the building itself stays visible behind it
    const at = toLngLat(o.georef, (Math.min(...xs) + Math.max(...xs)) / 2, Math.min(...ys))
    const marker = new maplibregl.Marker({ element: el, anchor: 'top' }).setLngLat(at).addTo(m)
    return { el, id: b.id, marker }
  })
  refreshLabels()
}

function refreshLabels() {
  for (const l of labels) {
    const b = objects.buildings.get(l.id)
    if (!b) continue
    const people = peopleIn(l.id)
    const sev = live.alertedObjects.get(l.id)
    // classList, not className: MapLibre keeps its own positioning classes on the marker element
    l.el.classList.toggle('sev-warning', sev === 'warning')
    l.el.classList.toggle('sev-critical', sev === 'critical')
    l.el.classList.toggle('on', selection.value?.id === l.id)
    l.el.innerHTML = ''
    const name = document.createElement('b')
    name.textContent = b.name
    l.el.append(name)
    if (people) {
      const p = document.createElement('span')
      p.textContent = `👤 ${people}`
      l.el.append(p)
    }
  }
}

// --- map lifecycle ---------------------------------------------------------------------------------
function fitSite(o: ObjectsResponse, animate = false) {
  const m = map.value
  if (!m) return
  const ring = lngLatRing(o.georef, o.site.coordinates[0] as [number, number][])
  const b = ring.reduce((acc, p) => acc.extend(p), new maplibregl.LngLatBounds(ring[0], ring[0]))
  m.fitBounds(b, {
    padding: 40,
    pitch: tilted.value ? PITCH : 0,
    bearing: tilted.value ? BEARING : 0,
    duration: animate ? 900 : 0,
  })
}

function init(o: ObjectsResponse) {
  const m = new maplibregl.Map({
    container: container.value!,
    style: baseStyle(),
    center: toLngLat(o.georef, (o.extent[0] + o.extent[2]) / 2, (o.extent[1] + o.extent[3]) / 2),
    zoom: 16,
    maxPitch: 75,
    attributionControl: { compact: true },
  })
  // the grid cell gets its size after the map is created (and changes with the side panel)
  resizer = new ResizeObserver(() => m.resize())
  resizer.observe(container.value!)
  m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
  m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')
  map.value = m
  m.on('load', () => {
    addFlatLayers(m, o)
    layer = new SiteLayer(o.georef)
    m.addLayer(layer)
    layer.setBuildings(o.buildings)
    layer.updateVehicles(live.vehicles.values(), objects.vehicles)
    layer.setAlerted(live.alertedObjects)
    addLabels(m, o)
    fitSite(o)
  })
  m.on('click', (e) => {
    const hit = layer?.pick(e.point.x, e.point.y)
    if (hit) return focus(hit)
    const s = m.queryRenderedFeatures(e.point, { layers: ['sensors'] })[0]
    if (s) return focus({ kind: 'sensor', id: String(s.properties.id) })
    selection.value = null
  })
  let hoverPending = false
  m.on('mousemove', (e) => {
    if (hoverPending) return
    hoverPending = true
    requestAnimationFrame(() => {
      hoverPending = false
      const over = !!layer?.pick(e.point.x, e.point.y) || m.queryRenderedFeatures(e.point, { layers: ['sensors'] }).length > 0
      m.getCanvas().style.cursor = over ? 'pointer' : ''
    })
  })
}

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
    for (const [id, data] of Object.entries(staticSources(o))) (m.getSource(id) as maplibregl.GeoJSONSource)?.setData(data)
    layer?.setBuildings(o.buildings)
    addLabels(m, o)
  },
)

watch(
  () => live.vehicles,
  (v) => layer?.updateVehicles(v.values(), objects.vehicles),
)
watch(
  () => [live.alertedObjects, live.people] as const,
  () => {
    layer?.setAlerted(live.alertedObjects)
    refreshLabels()
  },
)
// outdoor sensor colours: at most once a second, sensors report every few seconds anyway
let sensorsTimer = 0
watch(
  () => live.sensors,
  () => {
    if (sensorsTimer) return
    sensorsTimer = window.setTimeout(() => {
      sensorsTimer = 0
      const o = objects.data
      if (o && map.value?.getSource('sensors')) (map.value.getSource('sensors') as maplibregl.GeoJSONSource).setData(sensorsFC(o))
    }, 1000)
  },
)
watch(routePoints, (pts) => {
  const o = objects.data
  if (o && map.value?.getSource('route')) (map.value.getSource('route') as maplibregl.GeoJSONSource).setData(routeFC(o, pts))
})
watch(basemap, (b) => map.value?.isStyleLoaded() && setBasemap(map.value, b))
watch(selection, (sel) => {
  layer?.setSelected(sel && sel.kind !== 'sensor' ? sel : null)
  if (!sel) routePoints.value = null
  refreshLabels()
})

onBeforeUnmount(() => {
  window.clearTimeout(sensorsTimer)
  resizer?.disconnect()
  labels.forEach((l) => l.marker.remove())
  map.value?.remove()
  map.value = null
  layer = null
})

// --- selection -------------------------------------------------------------------------------------
function positionOf(sel: NonNullable<Selection>): [number, number] | null {
  if (sel.kind === 'sensor') {
    const s = objects.sensors.get(sel.id)
    if (s?.vehicle_id) return positionOf({ kind: 'vehicle', id: s.vehicle_id })
    return s?.geo ? [s.geo.x, s.geo.y] : null
  }
  return layer?.positionOf(sel) ?? null
}

function focus(sel: NonNullable<Selection>) {
  selection.value = sel
  const pos = positionOf(sel)
  const m = map.value
  if (!pos || !m || !objects.data) return
  m.flyTo({ center: toLngLat(objects.data.georef, ...pos), zoom: Math.max(m.getZoom(), 17.2), duration: 900 })
}

function locate(a: Alert) {
  const sel: Selection = a.vehicle_id
    ? { kind: 'vehicle', id: a.vehicle_id }
    : a.sensor_id
      ? { kind: 'sensor', id: a.sensor_id }
      : a.building_id
        ? { kind: 'building', id: a.building_id }
        : null
  if (sel) focus(sel)
}

function toggleTilt() {
  tilted.value = !tilted.value
  map.value?.easeTo({ pitch: tilted.value ? PITCH : 0, bearing: tilted.value ? BEARING : 0, duration: 700 })
}

// exposed for debugging from the console: plan metres under the cursor
defineExpose({ map, toLocal: (lng: number, lat: number) => objects.data && toLocal(objects.data.georef, lng, lat) })
</script>

<template>
  <div class="real-map">
    <div class="map-area">
      <div ref="container" class="map" />
      <div v-if="!objects.data" class="empty-state">Загрузка плана…</div>

      <div class="toolbar panel">
        <div class="stats">
          <div><b class="mono">{{ stats.moving }}</b>/{{ stats.total }}<small>в движении</small></div>
          <div><b class="mono">{{ stats.people }}</b><small>людей</small></div>
          <div :class="{ 'st-critical': live.criticalCount }"><b class="mono">{{ live.openAlerts.length }}</b><small>тревог</small></div>
        </div>
        <div class="row">
          <div class="seg">
            <button v-for="(label, key) in BASEMAPS" :key="key" class="small" :class="{ on: basemap === key }" @click="basemap = key">
              {{ label }}
            </button>
          </div>
          <button class="small" @click="toggleTilt">{{ tilted ? 'Вид сверху' : '3D' }}</button>
          <button class="small ghost" title="Показать всю площадку" @click="objects.data && fitSite(objects.data, true)">⤢</button>
        </div>
        <div class="legend">
          <span><i style="background: #fbbf24" />едет</span><span><i style="background: #38bdf8" />стоит, двигатель вкл.</span>
          <span><i style="background: #94a3b8" />заглушен</span><span><i style="background: #ef4444" />тревога</span>
        </div>
      </div>
    </div>

    <aside class="side">
      <ObjectCard
        v-if="selection"
        :selection="selection"
        @close="selection = null"
        @route="(p) => (routePoints = p)"
        @select="(s) => s && focus(s)"
      />
      <template v-else>
        <h2>Активные тревоги</h2>
        <div v-if="!live.openAlerts.length" class="empty-state">Тревог нет — всё в норме</div>
        <div class="feed">
          <AlertItem v-for="a in live.openAlerts" :key="a.id" :alert="a" compact @locate="locate" />
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.real-map {
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
.toolbar {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 2;
  padding: 10px 12px;
  display: grid;
  gap: 10px;
  max-width: calc(100% - 72px);
  background: #111a2cee;
}
.stats {
  display: flex;
  gap: 16px;
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
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 11px;
  color: var(--text-muted);
}
.legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  margin-right: 4px;
}
:deep(.bld-label) {
  display: grid;
  gap: 1px;
  padding: 3px 7px;
  border-radius: 6px;
  background: #0b1120d9;
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 11px;
  line-height: 1.25;
  white-space: nowrap;
  cursor: pointer;
  text-align: center;
}
:deep(.bld-label span) {
  color: var(--text-muted);
}
:deep(.bld-label.on) {
  border-color: var(--accent);
}
:deep(.bld-label.sev-warning) {
  border-color: var(--st-warning);
}
:deep(.bld-label.sev-critical) {
  border-color: var(--st-critical);
  box-shadow: 0 0 10px var(--st-critical);
}
:deep(.maplibregl-ctrl-attrib) {
  font-size: 10px;
}
@media (max-width: 900px) {
  .real-map {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 1fr 42%;
  }
  .side {
    border-left: none;
    border-top: 1px solid var(--border);
  }
  .toolbar {
    left: 8px;
    top: 8px;
    padding: 8px;
  }
  .legend {
    display: none;
  }
}
</style>
