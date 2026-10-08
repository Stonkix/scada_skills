<script setup lang="ts">
import L from 'leaflet'
import { inject, onBeforeUnmount, watch, watchEffect } from 'vue'
import type { Sensor, VehicleLive } from '@/api/types'
import { MAP_KEY, SENSOR_GLYPH, VEHICLE_SVG, centroid, ll, type Selection } from '@/lib/plan'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const props = defineProps<{
  floor: number
  layers: { sensors: boolean; vehicles: boolean; people: boolean; alerts: boolean }
  selected: Selection
}>()
const emit = defineEmits<{ select: [sel: Selection] }>()

const map = inject(MAP_KEY)!
const live = useLive()
const objects = useObjects()

const sensorLayer = L.layerGroup()
const vehicleLayer = L.layerGroup()
const peopleLayer = L.layerGroup()
const sensorMarkers = new Map<string, L.Marker>()
const vehicleMarkers = new Map<string, L.Marker>()
const peopleMarkers = new Map<string, L.Marker>()

function select(sel: Selection) {
  return (e: L.LeafletMouseEvent) => {
    L.DomEvent.stopPropagation(e)
    emit('select', sel)
  }
}

// --- sensors ------------------------------------------------------------------------------------

function sensorVisible(s: Sensor) {
  return s.geo && !s.is_mobile && (s.floor == null || s.floor === props.floor || s.building_id == null)
}

function buildSensors() {
  sensorLayer.clearLayers()
  sensorMarkers.clear()
  for (const s of objects.data?.sensors ?? []) {
    if (!sensorVisible(s) || !s.enabled) continue
    const m = L.marker(ll(s.geo!.x, s.geo!.y), {
      icon: L.divIcon({
        className: 'sensor-icon',
        html: `<div class="sensor">${SENSOR_GLYPH[s.type] ?? '?'}</div>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      }),
      zIndexOffset: 100,
    })
    m.bindTooltip(s.name, { direction: 'top', offset: [0, -8] })
    m.on('click', select({ kind: 'sensor', id: s.id }))
    m.addTo(sensorLayer)
    sensorMarkers.set(s.id, m)
  }
}

function paintSensors() {
  const alerted = props.layers.alerts ? live.alertedObjects : new Map()
  for (const [id, m] of sensorMarkers) {
    const el = m.getElement()?.firstElementChild as HTMLElement | undefined
    if (!el) continue
    const status = live.sensors.get(id)?.status ?? 'unknown'
    const alert = alerted.get(id)
    const sel = props.selected?.kind === 'sensor' && props.selected.id === id
    el.className = `sensor s-${status}${alert ? ` alert-${alert}` : ''}${sel ? ' selected' : ''}`
  }
}

// --- vehicles -----------------------------------------------------------------------------------

function vehicleIcon(kind: string, plate: string) {
  return L.divIcon({
    className: 'vehicle-icon',
    html: `<div class="vehicle"><svg viewBox="0 0 24 24" width="22" height="22">${VEHICLE_SVG[kind] ?? VEHICLE_SVG.truck}</svg><span>${plate}</span></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  })
}

function paintVehicles() {
  const alerted = props.layers.alerts ? live.alertedObjects : new Map()
  for (const [id, v] of live.vehicles as Map<string, VehicleLive>) {
    const meta = objects.vehicles.get(id)
    if (!meta) continue
    const onPlan = v.geo.x >= -10 // the public road beyond the gate is drawn a few metres only
    let m = vehicleMarkers.get(id)
    if (!m) {
      m = L.marker(ll(v.geo.x, v.geo.y), { icon: vehicleIcon(meta.kind, meta.plate), zIndexOffset: 500 })
      m.on('click', select({ kind: 'vehicle', id }))
      vehicleMarkers.set(id, m)
    }
    if (onPlan && !vehicleLayer.hasLayer(m)) m.addTo(vehicleLayer)
    if (!onPlan && vehicleLayer.hasLayer(m)) vehicleLayer.removeLayer(m)
    m.setLatLng(ll(v.geo.x, v.geo.y))
    const el = m.getElement()?.firstElementChild as HTMLElement | undefined
    if (!el) continue
    const alert = alerted.get(id)
    const sel = props.selected?.kind === 'vehicle' && props.selected.id === id
    el.className = `vehicle v-${v.status} k-${meta.kind}${alert ? ` alert-${alert}` : ''}${sel ? ' selected' : ''}`
    ;(el.firstElementChild as SVGElement).style.transform = `rotate(${v.heading_deg}deg)`
  }
}

// --- people per building ------------------------------------------------------------------------

function buildPeople() {
  peopleLayer.clearLayers()
  peopleMarkers.clear()
  for (const b of objects.data?.buildings ?? []) {
    const outline = b.geometry.coordinates[0] as [number, number][]
    const [cx, cy] = centroid(outline)
    const m = L.marker(ll(cx, cy), {
      icon: L.divIcon({ className: 'people-icon', html: '<div class="people"></div>', iconSize: [0, 0] }),
      zIndexOffset: 50,
    })
    m.on('click', select({ kind: 'building', id: b.id }))
    m.addTo(peopleLayer)
    peopleMarkers.set(b.id, m)
  }
}

function paintPeople() {
  const alerted = props.layers.alerts ? live.alertedObjects : new Map()
  for (const [id, m] of peopleMarkers) {
    const el = m.getElement()?.firstElementChild as HTMLElement | undefined
    if (!el) continue
    const n = live.people.get(id) ?? 0
    const alert = alerted.get(id)
    el.className = `people${n === 0 ? ' empty' : ''}${alert ? ` alert-${alert}` : ''}`
    el.innerHTML = `<svg viewBox="0 0 16 16" width="11" height="11"><circle cx="8" cy="5" r="3"/><path d="M2 15c0-3.3 2.7-6 6-6s6 2.7 6 6z"/></svg>${n}`
  }
}

// --- wiring -------------------------------------------------------------------------------------

function syncLayers() {
  const m = map.value
  if (!m) return
  for (const [layer, on] of [
    [sensorLayer, props.layers.sensors],
    [vehicleLayer, props.layers.vehicles],
    [peopleLayer, props.layers.people],
  ] as const) {
    if (on && !m.hasLayer(layer)) layer.addTo(m)
    if (!on && m.hasLayer(layer)) layer.remove()
  }
}

watch(() => [objects.data, props.floor], () => {
  buildSensors()
  buildPeople()
  syncLayers()
  paintSensors()
  paintPeople()
}, { immediate: true })
watch(() => ({ ...props.layers }), () => {
  syncLayers()
  paintSensors()
  paintPeople()
  paintVehicles()
})
watchEffect(paintSensors)
watchEffect(paintVehicles)
watchEffect(paintPeople)

onBeforeUnmount(() => {
  for (const layer of [sensorLayer, vehicleLayer, peopleLayer]) layer.remove()
})
</script>

<template><span hidden /></template>
