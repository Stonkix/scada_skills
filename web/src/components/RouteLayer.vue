<script setup lang="ts">
import L from 'leaflet'
import { inject, onBeforeUnmount, watch } from 'vue'
import type { RoutePoint } from '@/api/types'
import { MAP_KEY, ll } from '@/lib/plan'

const props = defineProps<{ points: RoutePoint[]; color?: string }>()
const map = inject(MAP_KEY)!
const layer = L.layerGroup()

watch(
  () => props.points,
  (pts) => {
    layer.clearLayers()
    const onSite = pts.filter((p) => p.x >= -10)
    if (!map.value || onSite.length < 2) return
    L.polyline(onSite.map((p) => ll(p.x, p.y)), { color: props.color ?? '#a78bfa', weight: 3, opacity: 0.85 }).addTo(layer)
    const first = onSite[0]
    L.circleMarker(ll(first.x, first.y), { radius: 5, color: '#a78bfa', fillOpacity: 1 })
      .bindTooltip(`Начало: ${new Date(first.ts).toLocaleTimeString('ru-RU')}`)
      .addTo(layer)
    layer.addTo(map.value)
  },
  { immediate: true },
)
onBeforeUnmount(() => layer.remove())
</script>

<template><span hidden /></template>
