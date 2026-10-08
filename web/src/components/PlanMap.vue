<script setup lang="ts">
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { onBeforeUnmount, onMounted, provide, ref, shallowRef, watch } from 'vue'
import type { ObjectsResponse } from '@/api/types'
import { GEOZONE_STYLE, MAP_KEY, boundsOf, centroid, ll, ring } from '@/lib/plan'

const props = withDefaults(
  defineProps<{
    objects: ObjectsResponse
    floor: number
    showZones?: boolean
    showRoads?: boolean
    fitTop?: number // px kept free at the top when fitting the plan (floating toolbars)
  }>(),
  { showZones: true, showRoads: true, fitTop: 20 },
)
const emit = defineEmits<{ building: [id: string]; background: [] }>()

const el = ref<HTMLDivElement>()
const map = shallowRef<L.Map | null>(null)
provide(MAP_KEY, map)

let staticLayer: L.LayerGroup | null = null
let roomsLayer: L.LayerGroup | null = null
let zonesLayer: L.LayerGroup | null = null
let roadsLayer: L.LayerGroup | null = null
const roadWidth = new WeakMap<L.Polyline, number>() // metres, to scale line weight with zoom

function drawStatic() {
  const m = map.value
  if (!m) return
  for (const layer of [staticLayer, roomsLayer, zonesLayer, roadsLayer]) layer?.remove()
  const o = props.objects
  staticLayer = L.layerGroup().addTo(m)
  roadsLayer = L.layerGroup()
  zonesLayer = L.layerGroup()

  L.polygon(ring(o.site.coordinates[0] as [number, number][]), {
    pane: 'site', color: '#334155', weight: 1, fillColor: '#111827', fillOpacity: 1, interactive: false,
  }).addTo(staticLayer)

  for (const r of o.roads) {
    // road width in metres -> pixels depends on zoom; weight is updated on zoom
    const line = L.polyline(r.geometry.coordinates.map(([x, y]) => ll(x, y)), {
      pane: 'roads', color: '#2c3646', weight: 6, lineCap: 'square', lineJoin: 'miter', interactive: false,
    }).addTo(roadsLayer)
    roadWidth.set(line, r.width_m)
  }
  for (const z of o.geozones) {
    if (z.zone_type === 'speed') continue
    L.polygon(ring(z.geometry.coordinates[0] as [number, number][]), { ...GEOZONE_STYLE[z.zone_type], pane: 'zones', interactive: false })
      .bindTooltip(z.name, { sticky: true })
      .addTo(zonesLayer)
  }
  for (const b of o.buildings) {
    const poly = L.polygon(ring(b.geometry.coordinates[0] as [number, number][]), {
      pane: 'buildings', color: '#64748b', weight: 1.5, fillColor: '#1e293b', fillOpacity: 1, className: 'building',
    })
    poly.on('click', (e) => {
      L.DomEvent.stopPropagation(e)
      emit('building', b.id)
    })
    poly.addTo(staticLayer)
    const outline = b.geometry.coordinates[0] as [number, number][]
    const [cx] = centroid(outline)
    const top = Math.max(...outline.map(([, y]) => y))
    L.marker(ll(cx, top - 4), {
      interactive: false,
      icon: L.divIcon({ className: 'building-label', html: `<span>${b.name}</span>`, iconSize: [0, 0] }),
    }).addTo(staticLayer)
  }
  for (const c of o.checkpoints) {
    L.marker(ll(c.x, c.y), {
      icon: L.divIcon({ className: 'checkpoint', html: '<span>КПП</span>', iconSize: [34, 16], iconAnchor: [17, 8] }),
    }).bindTooltip(c.name).addTo(staticLayer)
  }
  drawRooms()
  toggleOptional()
  scaleRoads()
}

function drawRooms() {
  const m = map.value
  if (!m) return
  roomsLayer?.remove()
  roomsLayer = L.layerGroup().addTo(m)
  for (const r of props.objects.rooms.filter((r) => r.floor === props.floor)) {
    L.polygon(ring(r.geometry.coordinates[0] as [number, number][]), {
      pane: 'rooms', color: '#475569', weight: 0.8, fill: false, dashArray: '2 3', interactive: false,
    }).addTo(roomsLayer)
  }
}

function toggleOptional() {
  const m = map.value
  if (!m || !roadsLayer || !zonesLayer) return
  if (props.showRoads) roadsLayer.addTo(m)
  else roadsLayer.remove()
  if (props.showZones) zonesLayer.addTo(m)
  else zonesLayer.remove()
}

/** Draw roads at their real width: pixels per metre = 2^zoom in CRS.Simple. */
function scaleRoads() {
  const m = map.value
  if (!m || !roadsLayer) return
  const pxPerM = 2 ** m.getZoom()
  roadsLayer.eachLayer((l) => {
    const line = l as L.Polyline
    line.setStyle({ weight: Math.max(2, (roadWidth.get(line) ?? 6) * pxPerM) })
  })
}

onMounted(() => {
  const m = L.map(el.value!, {
    crs: L.CRS.Simple,
    minZoom: -2,
    maxZoom: 3,
    zoomSnap: 0.25,
    attributionControl: false,
    zoomControl: true,
  })
  // fixed paint order regardless of which layers are toggled: site < roads < zones < buildings < rooms
  for (const [name, z] of [['site', 300], ['roads', 310], ['zones', 320], ['buildings', 330], ['rooms', 340]] as const) {
    m.createPane(name).style.zIndex = String(z)
  }
  const b = boundsOf(props.objects.extent as [number, number, number, number])
  m.fitBounds(b, { paddingTopLeft: [20, props.fitTop], paddingBottomRight: [20, 20] })
  m.setMaxBounds(b.pad(0.5))
  m.on('zoomend', scaleRoads)
  m.on('click', () => emit('background'))
  map.value = m
  drawStatic()
})

onBeforeUnmount(() => {
  map.value?.remove()
  map.value = null
})

watch(() => props.objects, drawStatic)
watch(() => props.floor, drawRooms)
watch(() => [props.showRoads, props.showZones], toggleOptional)

defineExpose({ map })
</script>

<template>
  <div class="plan-map">
    <div ref="el" class="plan-map__canvas"></div>
    <slot v-if="map" />
  </div>
</template>

<style>
.plan-map {
  position: relative;
  width: 100%;
  height: 100%;
}
.plan-map__canvas {
  position: absolute;
  inset: 0;
  background: var(--map-bg);
}
.building-label span {
  display: inline-block;
  transform: translate(-50%, -100%);
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-muted);
  text-shadow: 0 1px 2px #000;
  pointer-events: none;
}
.checkpoint span {
  display: block;
  font-size: 10px;
  font-weight: 700;
  text-align: center;
  line-height: 16px;
  color: #111;
  background: #f59e0b;
  border-radius: 3px;
}
.leaflet-container {
  font-family: inherit;
}
.leaflet-tooltip {
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--border);
  box-shadow: none;
}
.leaflet-tooltip::before {
  display: none;
}
.leaflet-bar a {
  background: var(--panel);
  color: var(--text);
  border-color: var(--border);
}
.leaflet-bar a:hover {
  background: var(--panel-2);
}
path.building:hover {
  fill: #253248;
  cursor: pointer;
}
</style>
