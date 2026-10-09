<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { Sensor } from '@/api/types'
import Sparkline from '@/components/Sparkline.vue'
import { SENSOR_STATUS_LABEL, fmtAgo, fmtValue, headline } from '@/lib/format'
import { useNow } from '@/lib/now'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

/** Sensor telemetry: a card per sensor with the live value, its trend and thresholds; filters by site and type. */
const objects = useObjects()
const live = useLive()
const router = useRouter()
const now = useNow()

const TYPES = [
  ['all', 'Все датчики'],
  ['climate', 'Климат'],
  ['smoke', 'Задымление'],
  ['motion', 'Движение'],
  ['access_control', 'СКУД'],
  ['anpr_camera', 'Камеры номеров'],
] as const
const type = ref<(typeof TYPES)[number][0]>('climate')
const site = ref('')
const onlyProblems = ref(false)

const statusOf = (id: string) =>
  live.alertedObjects.get(id) === 'critical' ? 'critical' : live.sensors.get(id)?.status ?? 'offline'
const sensors = computed(() =>
  (objects.data?.sensors ?? [])
    .filter((s) => s.enabled && !s.vehicle_id)
    .filter((s) => type.value === 'all' || s.type === type.value)
    .filter((s) => !site.value || objects.siteOfSensor(s.id) === site.value)
    .filter((s) => !onlyProblems.value || statusOf(s.id) !== 'ok')
    .sort((a, b) => rank(statusOf(b.id)) - rank(statusOf(a.id)) || a.name.localeCompare(b.name, 'ru')),
)
const RANK: Record<string, number> = { critical: 3, warning: 2, offline: 1, ok: 0 }
const rank = (s: string) => RANK[s] ?? 0

// --- trends: the last 3 h from history, then the live values appended ----------------------------------
/** The one metric each trended type plots. */
const TREND: Record<string, { metric: string; unit: string }> = {
  climate: { metric: 'temperature_c', unit: '°C' },
  smoke: { metric: 'smoke_pct', unit: '%/м' },
}
const series = ref(new Map<string, number[]>())
const loaded = new Set<string>()
const MAX_POINTS = 240

async function loadHistory(list: Sensor[]) {
  const todo = list.filter((s) => TREND[s.type] && !loaded.has(s.id))
  todo.forEach((s) => loaded.add(s.id))
  const to = new Date()
  const from = new Date(to.getTime() - 3 * 3600e3)
  for (let i = 0; i < todo.length; i += 6) {  // a few at a time: dozens of cards open at once
    await Promise.all(todo.slice(i, i + 6).map(async (s) => {
      const r = await api.GET('/sensors/{sensor_id}/history', {
        params: { path: { sensor_id: s.id }, query: { metric: TREND[s.type].metric, from: from.toISOString(), to: to.toISOString() } },
      })
      const pts = (r.data?.points ?? []).map((p) => p.avg)
      series.value.set(s.id, [...pts, ...(series.value.get(s.id) ?? [])].slice(-MAX_POINTS))
      series.value = new Map(series.value)
    }))
  }
}
watch(sensors, (list) => void loadHistory(list), { immediate: true })

const lastSeen = new Map<string, string>()
watch(
  () => live.revision,
  () => {
    let changed = false
    for (const [id, s] of live.sensors) {
      const trend = TREND[objects.sensors.get(id)?.type ?? '']
      const t = trend && s.values[trend.metric]
      if (typeof t !== "number" || lastSeen.get(id) === (s.last_seen ?? "")) continue
      lastSeen.set(id, s.last_seen ?? "")
      series.value.set(id, [...(series.value.get(id) ?? []), t].slice(-MAX_POINTS))
      changed = true
    }
    if (changed) series.value = new Map(series.value)
  },
)
onBeforeUnmount(() => lastSeen.clear())

const threshold = (s: Sensor) => s.thresholds.find((t) => t.metric === TREND[s.type]?.metric)
const COLOR: Record<string, string> = { ok: 'var(--st-ok)', warning: 'var(--st-warning)', critical: 'var(--st-critical)', offline: 'var(--st-offline)' }
const zoneName = (s: Sensor) => (s.zone_id ? objects.zoneNames.get(s.zone_id) ?? s.zone_id : '')
const siteName = (s: Sensor) => objects.sites.get(objects.siteOfSensor(s.id) ?? '')?.name ?? ''
const show = (id: string) => router.push({ path: '/', query: { sensor: id } })
const counts = computed(() => {
  const c = { ok: 0, warning: 0, critical: 0, offline: 0 }
  for (const s of sensors.value) c[statusOf(s.id) as keyof typeof c]++
  return c
})
</script>

<template>
  <div class="page">
    <header class="row wrap">
      <h1 class="grow">Телеметрия датчиков</h1>
      <span class="muted small">
        <span class="st-ok">● {{ counts.ok }}</span> · <span class="st-warning">● {{ counts.warning }}</span> ·
        <span class="st-critical">● {{ counts.critical }}</span> · <span class="muted">● {{ counts.offline }} нет связи</span>
      </span>
      <select v-model="site" aria-label="Площадка">
        <option value="">Все площадки</option>
        <option v-for="s in objects.data?.sites ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
    </header>
    <div class="chips">
      <button v-for="[k, label] in TYPES" :key="k" :class="{ on: type === k }" @click="type = k">{{ label }}</button>
      <label class="toggle"><input v-model="onlyProblems" type="checkbox" /> только не в норме</label>
    </div>

    <div class="cards">
      <button v-for="s in sensors" :key="s.id" class="panel card" :class="`s-${statusOf(s.id)}`" title="Показать на карте" @click="show(s.id)">
        <div class="row">
          <span class="grow name">{{ s.name }}</span>
          <span class="badge" :class="`st-${statusOf(s.id)}`">{{ SENSOR_STATUS_LABEL[statusOf(s.id) as keyof typeof SENSOR_STATUS_LABEL] ?? 'Нет данных' }}</span>
        </div>
        <div class="value mono">
          <template v-if="s.type === 'climate'">
            {{ fmtValue(live.sensors.get(s.id)?.values.temperature_c, '') }}<small> °C</small>
            <small class="hum">{{ fmtValue(live.sensors.get(s.id)?.values.humidity_pct, '%') }}</small>
          </template>
          <template v-else>{{ headline(s.type, live.sensors.get(s.id)?.values) }}</template>
        </div>
        <div class="muted small">{{ zoneName(s) }} · {{ siteName(s) }}</div>
        <Sparkline v-if="TREND[s.type]" :values="series.get(s.id) ?? []" :color="COLOR[statusOf(s.id)]"
                   :min="threshold(s)?.min" :max="threshold(s)?.max" />
        <div class="muted small foot">
          <span v-if="threshold(s)">норма {{ threshold(s)!.min ?? '−∞' }}…{{ threshold(s)!.max ?? '+∞' }} {{ TREND[s.type]?.unit }},
            авария вне {{ threshold(s)!.critical_min ?? '−∞' }}…{{ threshold(s)!.critical_max ?? '+∞' }}</span>
          <span class="grow" />
          <span>{{ fmtAgo(live.sensors.get(s.id)?.last_seen, now) }}</span>
        </div>
      </button>
    </div>
    <div v-if="!sensors.length" class="empty-state">Датчиков по фильтру нет</div>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  overflow: auto;
  padding: 16px 18px 28px;
  display: grid;
  gap: 14px;
  align-content: start;
}
.chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.chips button {
  border-radius: 99px;
  padding: 4px 12px;
  font-size: 13px;
}
.chips button.on {
  background: var(--accent-2);
  border-color: var(--accent-2);
  color: #04121f;
}
.toggle {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  margin-left: 8px;
}
.cards {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
}
.card {
  display: grid;
  gap: 4px;
  text-align: left;
  padding: 12px 14px;
  white-space: normal;
  align-content: start;
  border-left: 3px solid var(--st-ok);
  color: var(--text);
}
.card.s-warning {
  border-left-color: var(--st-warning);
}
.card.s-critical {
  border-left-color: var(--st-critical);
  box-shadow: 0 0 0 1px #ef444433;
}
.card.s-offline {
  border-left-color: var(--st-offline);
}
.name {
  font-size: 13px;
}
.value {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
}
.value small {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: 400;
}
.value .hum {
  margin-left: 8px;
}
.small {
  font-size: 11px;
}
.foot {
  display: flex;
  gap: 6px;
}
</style>
