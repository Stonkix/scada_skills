<script setup lang="ts">
import L from 'leaflet'
import { inject, onBeforeUnmount, watch } from 'vue'
import { MAP_KEY, VEHICLE_SVG, ll } from '@/lib/plan'
import { positionAt, trail, type Track } from '@/lib/replay'
import { useObjects } from '@/stores/objects'

const props = defineProps<{ tracks: Track[]; time: number; trailMs?: number }>()
const map = inject(MAP_KEY)!
const objects = useObjects()
const layer = L.layerGroup()
const markers = new Map<string, { marker: L.Marker; line: L.Polyline; heading: number }>()

function draw() {
  if (!map.value) return
  if (!map.value.hasLayer(layer)) layer.addTo(map.value)
  const live = new Set<string>()
  for (const tr of props.tracks) {
    const p = positionAt(tr, props.time)
    let m = markers.get(tr.id)
    if (!p || p.x < -10) {
      if (m) {
        layer.removeLayer(m.marker)
        layer.removeLayer(m.line)
      }
      continue
    }
    live.add(tr.id)
    const meta = objects.vehicles.get(tr.id)
    if (!m) {
      const icon = L.divIcon({
        className: 'vehicle-icon replay',
        html: `<div class="vehicle k-${meta?.kind ?? 'truck'}"><svg viewBox="0 0 24 24" width="22" height="22">${VEHICLE_SVG[meta?.kind ?? 'truck']}</svg><span>${meta?.plate ?? tr.id}</span></div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      })
      m = {
        marker: L.marker(ll(p.x, p.y), { icon, zIndexOffset: 500 }),
        line: L.polyline([], { color: '#94a3b8', weight: 2, opacity: 0.5, interactive: false }),
        heading: 0,
      }
      markers.set(tr.id, m)
    }
    if (!layer.hasLayer(m.marker)) m.marker.addTo(layer)
    if (!layer.hasLayer(m.line)) m.line.addTo(layer)
    m.marker.setLatLng(ll(p.x, p.y))
    if (!Number.isNaN(p.heading)) m.heading = p.heading
    const svg = m.marker.getElement()?.querySelector('svg')
    if (svg) svg.style.transform = `rotate(${m.heading}deg)`
    m.line.setLatLngs(trail(tr, props.time, props.trailMs ?? 120_000).filter(([x]) => x >= -10).map(([x, y]) => ll(x, y)))
  }
}

watch(() => [props.tracks, props.time], draw, { immediate: true })
onBeforeUnmount(() => layer.remove())
</script>

<template><span hidden /></template>

<style>
/* scrubbing must not animate between frames */
.leaflet-marker-icon.vehicle-icon.replay {
  transition: none;
}
</style>
