<script setup lang="ts">
import { computed } from 'vue'

/** A tiny trend line; optional dashed bounds (warning limits) when they fall inside the range. */
const props = defineProps<{ values: number[]; color?: string; min?: number | null; max?: number | null }>()

const view = computed(() => {
  const v = props.values
  if (v.length < 2) return null
  const lo = Math.min(...v)
  const hi = Math.max(...v)
  const pad = (hi - lo || 1) * 0.15
  const a = lo - pad
  const b = hi + pad
  const y = (x: number) => 28 - ((x - a) / (b - a)) * 26
  const points = v.map((x, i) => `${((i / (v.length - 1)) * 100).toFixed(1)},${y(x).toFixed(1)}`).join(' ')
  const bounds = [props.min, props.max].filter((x): x is number => x != null && x > a && x < b).map(y)
  return { points, bounds }
})
</script>

<template>
  <svg v-if="view" class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
    <line v-for="(by, i) in view.bounds" :key="i" x1="0" x2="100" :y1="by" :y2="by" class="bound" />
    <polyline :points="view.points" fill="none" :stroke="color ?? 'var(--accent)'" stroke-width="1.6" vector-effect="non-scaling-stroke" />
  </svg>
  <div v-else class="spark empty" />
</template>

<style scoped>
.spark {
  width: 100%;
  height: 32px;
  display: block;
}
.bound {
  stroke: var(--st-warning);
  stroke-width: 1;
  stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke;
  opacity: 0.6;
}
</style>
