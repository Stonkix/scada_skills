<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { PredictResponse } from '@/api/types'
import { fmtValue } from '@/lib/format'

const props = defineProps<{ sensorId: string; metric?: string }>()
const emit = defineEmits<{ forecast: [points: { ts: string; value: number }[]] }>()

const data = ref<PredictResponse | null>(null)
const error = ref('')

const RISK = {
  ok: { label: 'Норма', cls: 'st-ok' },
  watch: { label: 'Наблюдать', cls: 'sev-info' },
  warning: { label: 'Внимание', cls: 'sev-warning' },
  critical: { label: 'Срочно', cls: 'sev-critical' },
} as const

async function load() {
  error.value = ''
  try {
    data.value = unwrap(await api.GET('/predict/{sensor_id}', {
      params: { path: { sensor_id: props.sensorId }, query: props.metric ? { metric: props.metric } : {} },
    }))
    emit('forecast', data.value.prediction?.forecast ?? [])
  } catch (e) {
    error.value = errorText(e)
    emit('forecast', [])
  }
}
watch(() => [props.sensorId, props.metric], load, { immediate: true })
const timer = setInterval(load, 60_000)
onBeforeUnmount(() => clearInterval(timer))

const eta = (h: number | null | undefined) =>
  h == null ? '—' : h === 0 ? 'уже' : h < 1 ? `${Math.max(1, Math.round(h * 60))} мин` : h < 48 ? `${h.toFixed(1)} ч` : `${(h / 24).toFixed(1)} сут`
</script>

<template>
  <section v-if="data && (data.prediction || data.maintenance)" class="forecast">
    <template v-if="data.prediction">
      <div class="row">
        <h3 class="grow">Прогноз</h3>
        <span class="badge" :class="RISK[data.prediction.risk].cls">{{ RISK[data.prediction.risk].label }}</span>
      </div>
      <p class="summary">{{ data.prediction.summary }}</p>
      <div class="kv">
        <span class="muted">Тренд</span>
        <span class="mono">{{ data.prediction.trend_per_hour == null ? 'мало данных' : `${fmtValue(data.prediction.trend_per_hour)} ${data.prediction.unit ?? ''}/ч` }}</span>
        <span class="muted">От суточной нормы</span>
        <span class="mono">{{ data.prediction.zscore == null ? '—' : `${data.prediction.zscore > 0 ? '+' : ''}${data.prediction.zscore.toFixed(1)}σ` }}</span>
        <span class="muted">До выхода из нормы</span><span class="mono">{{ eta(data.prediction.eta_warning_h) }}</span>
        <span class="muted">До критической границы</span><span class="mono">{{ eta(data.prediction.eta_critical_h) }}</span>
      </div>
    </template>
    <template v-if="data.maintenance">
      <h3>Техобслуживание</h3>
      <p class="summary">{{ data.maintenance.summary }}</p>
      <div class="bar" :title="`${data.maintenance.remaining_km} км до ТО`">
        <i :style="{ width: `${100 - (100 * data.maintenance.remaining_km) / data.maintenance.service_interval_km}%` }" />
      </div>
    </template>
    <small class="muted">Линейный тренд по минутным агрегатам (30 мин при выраженном росте/падении, иначе 2 ч), отклонение — от
      суточной нормы. Обновляется раз в минуту.</small>
  </section>
  <div v-else-if="error" class="muted">{{ error }}</div>
</template>

<style scoped>
.forecast { display: grid; gap: 6px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); }
.summary { margin: 0; font-size: 13px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; font-size: 12px; }
.kv span:nth-child(even) { text-align: right; }
.bar { height: 6px; border-radius: 3px; background: var(--panel-2); overflow: hidden; }
.bar i { display: block; height: 100%; background: #3987e5; }
small { font-size: 11px; }
</style>
