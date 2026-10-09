<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { SensorHistory, Threshold } from '@/api/types'
import { AXIS, TOOLTIP, VChart } from '@/lib/echarts'

const props = defineProps<{
  sensorId: string
  metric: string
  unit?: string | null
  threshold?: Threshold
  forecast?: { ts: string; value: number }[] // trend projection, drawn dashed after "now"
}>()

const RANGES = [
  { label: '15 мин', ms: 15 * 60e3 },
  { label: '1 ч', ms: 3600e3 },
  { label: '6 ч', ms: 6 * 3600e3 },
  { label: '24 ч', ms: 24 * 3600e3 },
]
const range = ref(RANGES[1].ms)
const data = ref<SensorHistory | null>(null)
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const to = new Date()
    const from = new Date(to.getTime() - range.value)
    data.value = unwrap(
      await api.GET('/sensors/{sensor_id}/history', {
        params: { path: { sensor_id: props.sensorId }, query: { metric: props.metric, from: from.toISOString(), to: to.toISOString() } },
      }),
    )
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}
watch(() => [props.sensorId, props.metric, range.value], load, { immediate: true })

const option = computed(() => {
  const pts = data.value?.points ?? []
  const th = props.threshold
  const lines = [
    th?.max != null && { yAxis: th.max, name: 'макс', lineStyle: { color: '#f59e0b' } },
    th?.min != null && { yAxis: th.min, name: 'мин', lineStyle: { color: '#f59e0b' } },
    th?.critical_max != null && { yAxis: th.critical_max, name: 'крит', lineStyle: { color: '#ef4444' } },
    th?.critical_min != null && { yAxis: th.critical_min, name: 'крит', lineStyle: { color: '#ef4444' } },
  ].filter(Boolean)
  const aggregated = data.value?.step !== 'raw'
  return {
    animation: false,
    grid: { left: 44, right: 46, top: 14, bottom: 26 },
    tooltip: { ...TOOLTIP, valueFormatter: (v: number) => `${v?.toFixed?.(2)} ${props.unit ?? ''}` },
    xAxis: {
      type: 'time', ...AXIS, splitLine: { show: false },
      axisLabel: { ...AXIS.axisLabel, hideOverlap: true, formatter: range.value > 24 * 3600e3 - 1 ? '{dd}.{MM} {HH}:{mm}' : '{HH}:{mm}' },
    },
    yAxis: { type: 'value', scale: true, ...AXIS },
    series: [
      ...(aggregated
        ? [
            { type: 'line', name: 'мин', data: pts.map((p) => [p.ts, p.min]), lineStyle: { opacity: 0 }, symbol: 'none', stack: 'band' },
            { type: 'line', name: 'размах', data: pts.map((p) => [p.ts, p.max - p.min]), lineStyle: { opacity: 0 }, symbol: 'none',
              stack: 'band', areaStyle: { color: '#38bdf822' }, tooltip: { show: false } },
          ]
        : []),
      {
        type: 'line', name: aggregated ? 'среднее' : 'значение', data: pts.map((p) => [p.ts, p.avg]),
        showSymbol: false, lineStyle: { width: 1.6, color: '#38bdf8' }, itemStyle: { color: '#38bdf8' },
        markLine: { symbol: 'none', silent: true, label: { color: '#8a9ab5', fontSize: 10, formatter: '{b}' }, data: lines },
      },
      ...(props.forecast?.length
        ? [{ type: 'line', name: 'прогноз', data: props.forecast.map((p) => [p.ts, p.value]), showSymbol: false,
             lineStyle: { width: 1.6, type: 'dashed', color: '#d95926' }, itemStyle: { color: '#d95926' } }]
        : []),
    ],
  }
})
</script>

<template>
  <div class="history">
    <div class="row">
      <div class="seg">
        <button v-for="r in RANGES" :key="r.ms" class="small" :class="{ on: range === r.ms }" @click="range = r.ms">{{ r.label }}</button>
      </div>
      <span class="spacer" />
      <small class="muted">{{ loading ? 'загрузка…' : data ? `шаг ${data.step}, точек ${data.points.length}` : '' }}</small>
    </div>
    <div v-if="error" class="muted">{{ error }}</div>
    <div v-else-if="data && !data.points.length" class="empty-state">Нет данных за период</div>
    <VChart v-else class="chart" :option="option" autoresize />
  </div>
</template>

<style scoped>
.history { display: grid; gap: 6px; }
.chart { height: 180px; }
.seg { display: flex; gap: 2px; }
.seg button.on { border-color: var(--accent); color: var(--accent); }
</style>
