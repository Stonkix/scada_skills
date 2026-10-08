<script setup lang="ts">
import L from 'leaflet'
import { inject, onBeforeUnmount, watch } from 'vue'
import { PLACEABLE, type Feature, type Layout, type Mode, type Pt } from '@/lib/editor'
import { MAP_KEY, SENSOR_GLYPH, ll, ring } from '@/lib/plan'

const props = defineProps<{ layout: Layout; floor: number; mode: Mode; selected: string | null; showCoverage: boolean }>()
const emit = defineEmits<{
  select: [id: string | null]
  place: [p: Pt]
  move: [id: string, p: Pt]
  room: [a: Pt, b: Pt]
}>()

const map = inject(MAP_KEY)!
const layer = L.layerGroup()
let draft: L.Rectangle | null = null
let start: L.LatLng | null = null

const toPt = (latlng: L.LatLng): Pt => [latlng.lng, latlng.lat]

function visible(f: Feature): boolean {
  const floor = f.properties.floor as number | null | undefined
  return floor == null || floor === props.floor
}

function draw() {
  const m = map.value
  if (!m) return
  layer.clearLayers()
  if (!m.hasLayer(layer)) layer.addTo(m)
  for (const f of props.layout.features) {
    if (f.properties.kind === 'room' && f.properties.floor === props.floor) {
      const sel = f.id === props.selected
      const poly = L.polygon(ring((f.geometry.coordinates as Pt[][])[0]), {
        pane: 'rooms', color: sel ? '#38bdf8' : '#64748b', weight: sel ? 2.5 : 1, dashArray: sel ? undefined : '3 3',
        fillColor: '#38bdf8', fillOpacity: sel ? 0.12 : 0.02, interactive: props.mode === 'select',
      })
      poly.bindTooltip(String(f.properties.name), { sticky: true })
      poly.on('click', (e) => {
        L.DomEvent.stopPropagation(e)
        emit('select', f.id)
      })
      poly.addTo(layer)
    }
  }
  for (const f of props.layout.features) {
    if (f.properties.kind !== 'sensor' || !visible(f)) continue
    const [x, y] = f.geometry.coordinates as Pt
    const sel = f.id === props.selected
    // plans drawn before coverage existed have none: fall back to the typical radius of the type
    const radius = Number(f.properties.coverage_m ?? PLACEABLE.find((t) => t.type === f.properties.sensor_type)?.coverage ?? 0)
    if (props.showCoverage && radius) {
      L.circle(ll(x, y), {
        radius, color: sel ? '#38bdf8' : '#3987e5', weight: 1, opacity: 0.5,
        fillOpacity: sel ? 0.14 : 0.06, interactive: false,
      }).addTo(layer)
    }
    const marker = L.marker(ll(x, y), {
      draggable: props.mode === 'select',
      icon: L.divIcon({
        className: 'sensor-icon',
        html: `<div class="sensor s-edit${sel ? ' selected' : ''}">${SENSOR_GLYPH[String(f.properties.sensor_type)] ?? '?'}</div>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      }),
      zIndexOffset: sel ? 1000 : 100,
    })
    marker.bindTooltip(`${f.id}`, { direction: 'top', offset: [0, -8] })
    marker.on('click', (e) => {
      L.DomEvent.stopPropagation(e)
      emit('select', f.id)
    })
    marker.on('dragend', () => emit('move', f.id, toPt(marker.getLatLng())))
    marker.addTo(layer)
  }
}

// --- placing and drawing ----------------------------------------------------------------------

function onClick(e: L.LeafletMouseEvent) {
  if (props.mode === 'select') emit('select', null)
  else if (props.mode !== 'room') emit('place', toPt(e.latlng))
}
function onDown(e: L.LeafletMouseEvent) {
  if (props.mode !== 'room') return
  start = e.latlng
  draft = L.rectangle(L.latLngBounds(start, start), { color: '#38bdf8', weight: 1.5, dashArray: '4 3', fillOpacity: 0.1 }).addTo(map.value!)
}
function onMoveMouse(e: L.LeafletMouseEvent) {
  if (draft && start) draft.setBounds(L.latLngBounds(start, e.latlng))
}
function onUp(e: L.LeafletMouseEvent) {
  if (!draft || !start) return
  draft.remove()
  draft = null
  emit('room', toPt(start), toPt(e.latlng))
  start = null
}

function syncMode() {
  const m = map.value
  if (!m) return
  const drawing = props.mode === 'room'
  if (drawing) m.dragging.disable()
  else m.dragging.enable()
  m.getContainer().style.cursor = props.mode === 'select' ? '' : 'crosshair'
}

watch(
  map,
  (m, old) => {
    old?.off('click', onClick).off('mousedown', onDown).off('mousemove', onMoveMouse).off('mouseup', onUp)
    m?.on('click', onClick).on('mousedown', onDown).on('mousemove', onMoveMouse).on('mouseup', onUp)
    syncMode()
    draw()
  },
  { immediate: true },
)
watch(() => [props.layout, props.floor, props.selected, props.mode, props.showCoverage], draw, { deep: true })
watch(() => props.mode, syncMode)

onBeforeUnmount(() => {
  const m = map.value
  m?.off('click', onClick).off('mousedown', onDown).off('mousemove', onMoveMouse).off('mouseup', onUp)
  if (m) {
    m.dragging.enable()
    m.getContainer().style.cursor = ''
  }
  draft?.remove()
  layer.remove()
})
</script>

<template><span hidden /></template>

<style>
.sensor.s-edit {
  background: #38bdf8;
  cursor: grab;
}
.leaflet-dragging .sensor.s-edit {
  cursor: grabbing;
}
</style>
