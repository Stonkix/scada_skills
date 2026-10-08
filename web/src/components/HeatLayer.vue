<script setup lang="ts">
import L from 'leaflet'
import { inject, onBeforeUnmount, watch } from 'vue'
import type { Heatmap } from '@/api/types'
import { MAP_KEY, ll } from '@/lib/plan'

const props = defineProps<{ heat: Heatmap }>()
const map = inject(MAP_KEY)!
const layer = L.layerGroup()

/** Sequential, single hue: opacity grows with density (sqrt so sparse cells stay visible). */
function intensity(count: number, max: number): number {
  return max > 0 ? 0.08 + 0.82 * Math.sqrt(count / max) : 0
}

watch(
  () => props.heat,
  (h) => {
    layer.clearLayers()
    if (!map.value) return
    const half = h.cell_m / 2
    for (const c of h.cells) {
      L.rectangle([ll(c.x - half, c.y - half), ll(c.x + half, c.y + half)], {
        stroke: false,
        fillColor: '#3987e5',
        fillOpacity: intensity(c.count, h.max_count),
        interactive: true,
      })
        .bindTooltip(`${c.count} замеров`, { sticky: true })
        .addTo(layer)
    }
    layer.addTo(map.value)
  },
  { immediate: true },
)
onBeforeUnmount(() => layer.remove())
</script>

<template><span hidden /></template>
