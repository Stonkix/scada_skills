<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorText, unwrap } from '@/api/client'
import type { KpiResponse, Prediction } from '@/api/types'
import { AXIS, PALETTE, STATUS, SURFACE, TOOLTIP, VChart } from '@/lib/echarts'
import { SEVERITY_LABEL, fmtDuration } from '@/lib/format'
import { useObjects } from '@/stores/objects'

const objects = useObjects()
const router = useRouter()
const PERIODS = [
  { label: '1 час', h: 1 },
  { label: '8 часов', h: 8 },
  { label: 'Сутки', h: 24 },
  { label: 'Неделя', h: 168 },
]
const hours = ref(1)
const data = ref<KpiResponse | null>(null)
const error = ref('')
const loading = ref(false)
const showTable = ref(false)
const risks = ref<Prediction[]>([])
const RISK = { ok: ['Норма', 'st-ok'], watch: ['Наблюдать', 'sev-info'], warning: ['Внимание', 'sev-warning'], critical: ['Срочно', 'sev-critical'] } as const

async function load() {
  loading.value = true
  error.value = ''
  try {
    const to = new Date()
    const from = new Date(to.getTime() - hours.value * 3600e3)
    const [kpi, r] = await Promise.all([
      api.GET('/kpi', { params: { query: { from: from.toISOString(), to: to.toISOString() } } }).then(unwrap),
      api.GET('/predict', { params: { query: { sensor_type: 'climate', risk_at_least: 'watch' } } }).then(unwrap).catch(() => []),
    ])
    data.value = kpi
    risks.value = r
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}
watch(hours, load, { immediate: true })

const tiles = computed(() => {
  const d = data.value
  if (!d) return []
  const v = d.vehicles
  const moving = v.reduce((s, x) => s + x.moving_min, 0)
  const total = v.reduce((s, x) => s + x.moving_min + x.idle_min + x.stopped_min, 0)
  return [
    { label: 'Пробег техники', value: `${v.reduce((s, x) => s + x.mileage_km, 0).toFixed(1)} км` },
    { label: 'Загрузка техники', value: total ? `${Math.round((100 * moving) / total)} %` : '—', hint: 'доля времени в движении на территории' },
    { label: 'Въезды / выезды КПП', value: `${d.gate.entries} / ${d.gate.exits}` },
    { label: 'Проходы по СКУД', value: String(d.buildings.reduce((s, b) => s + b.entries, 0)) },
    { label: 'Тревог за период', value: String(d.alerts.opened) },
    { label: 'Среднее время реакции', value: fmtDuration(d.alerts.mean_ack_s), hint: 'от открытия до подтверждения' },
    { label: 'Открыто сейчас', value: String(d.alerts.open_now), alert: d.alerts.open_now > 0 },
  ]
})

const bar = { barMaxWidth: 22, itemStyle: { borderRadius: [4, 4, 0, 0], borderColor: SURFACE, borderWidth: 1 } }
const hbar = { barMaxWidth: 16, itemStyle: { borderRadius: [0, 4, 4, 0], borderColor: SURFACE, borderWidth: 1 } }
const legend = { top: 0, right: 0, textStyle: { color: '#8a9ab5' }, itemWidth: 12, itemHeight: 8 }

const gateOption = computed(() => {
  const gate = data.value?.gate
  const rows = gate?.by_hour ?? []
  const daily = (gate?.bucket_minutes ?? 60) >= 1440
  const label = (iso: string) =>
    new Date(iso).toLocaleString('ru-RU', daily ? { day: '2-digit', month: '2-digit' } : { hour: '2-digit', minute: '2-digit' })
  return {
    color: PALETTE,
    animation: false,
    legend,
    grid: { left: 36, right: 12, top: 30, bottom: 28 },
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: rows.map((r) => label(r.hour)), ...AXIS },
    yAxis: { type: 'value', minInterval: 1, ...AXIS },
    series: [
      { name: 'Въезды', type: 'bar', data: rows.map((r) => r.entries), ...bar },
      { name: 'Выезды', type: 'bar', data: rows.map((r) => r.exits), ...bar },
    ],
  }
})

const fleetOption = computed(() => {
  const v = [...(data.value?.vehicles ?? [])].filter((x) => x.moving_min + x.idle_min + x.stopped_min > 0)
  v.sort((a, b) => a.moving_min - b.moving_min)
  return {
    color: PALETTE,
    animation: false,
    legend,
    grid: { left: 92, right: 16, top: 30, bottom: 24 },
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' }, valueFormatter: (x: number) => `${x} мин` },
    xAxis: { type: 'value', ...AXIS, name: 'мин', nameTextStyle: { color: '#8a9ab5' } },
    yAxis: { type: 'category', data: v.map((x) => x.plate), ...AXIS, axisLabel: { ...AXIS.axisLabel, fontFamily: 'monospace' } },
    series: [
      { name: 'В движении', type: 'bar', stack: 't', data: v.map((x) => x.moving_min), ...hbar },
      { name: 'Стоит, двигатель работает', type: 'bar', stack: 't', data: v.map((x) => x.idle_min), ...hbar },
      { name: 'Стоит, заглушен', type: 'bar', stack: 't', data: v.map((x) => x.stopped_min), ...hbar },
    ],
  }
})

const buildingOption = computed(() => {
  const b = [...(data.value?.buildings ?? [])].sort((x, y) => x.entries - y.entries)
  return {
    color: [PALETTE[0]],
    animation: false,
    grid: { left: 150, right: 30, top: 10, bottom: 24 },
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', minInterval: 1, ...AXIS },
    yAxis: { type: 'category', data: b.map((x) => x.name), ...AXIS },
    series: [{ name: 'Проходов внутрь', type: 'bar', data: b.map((x) => x.entries), ...hbar,
               label: { show: true, position: 'right', color: '#8a9ab5', fontSize: 11 } }],
  }
})

const severityOption = computed(() => {
  const by = data.value?.alerts.by_severity ?? {}
  const keys = (['critical', 'warning', 'info'] as const).filter((k) => by[k])
  return {
    animation: false,
    grid: { left: 110, right: 30, top: 6, bottom: 20 },
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', minInterval: 1, ...AXIS },
    yAxis: { type: 'category', data: keys.map((k) => SEVERITY_LABEL[k]), ...AXIS },
    series: [{
      name: 'Тревог', type: 'bar', ...hbar,
      data: keys.map((k) => ({ value: by[k], itemStyle: { ...hbar.itemStyle, color: STATUS[k] } })),
      label: { show: true, position: 'right', color: '#8a9ab5', fontSize: 11 },
    }],
  }
})

const ruleNames = computed(() => new Map<string, string>([
  ['rule-threshold', 'Выход за пороги'], ['rule-speed', 'Превышение скорости'], ['rule-whitelist-plate', 'Номер вне базы'],
  ['rule-whitelist-card', 'Пропуск вне базы'], ['rule-after-hours', 'Движение без СКУД'], ['rule-breakdown', 'Простой техники'],
  ['rule-offline', 'Нет связи'], ['rule-geozone-garage', 'Въезд в ремзону'],
]))
</script>

<template>
  <div class="page">
    <div class="row wrap">
      <h1 class="grow">Показатели</h1>
      <div class="seg">
        <button v-for="p in PERIODS" :key="p.h" class="small" :class="{ on: hours === p.h }" @click="hours = p.h">{{ p.label }}</button>
      </div>
      <button class="small ghost" :disabled="loading" @click="load">{{ loading ? 'обновление…' : '⟳ Обновить' }}</button>
    </div>
    <div v-if="error" class="panel empty-state">{{ error }}</div>

    <template v-if="data">
      <div class="tiles">
        <div v-for="t in tiles" :key="t.label" class="panel tile" :title="t.hint">
          <div class="muted">{{ t.label }}</div>
          <div class="value mono" :class="{ 'st-critical': t.alert }">{{ t.value }}</div>
        </div>
      </div>

      <div class="charts">
        <section class="panel box">
          <h2>КПП-1: въезды и выезды{{ data.gate.bucket_minutes === 5 ? ' по 5 минут' : data.gate.bucket_minutes === 60 ? ' по часам' : ' по дням' }}</h2>
          <div v-if="!data.gate.by_hour.length" class="empty-state">Проездов за период не было</div>
          <VChart v-else class="chart" :option="gateOption" autoresize />
          <small class="muted">Номеров распознано камерами: {{ data.gate.plates_recognized }}</small>
        </section>

        <section class="panel box">
          <div class="row">
            <h2 class="grow">Время техники на территории</h2>
            <button class="small ghost" @click="showTable = !showTable">{{ showTable ? 'график' : 'таблица' }}</button>
          </div>
          <VChart v-if="!showTable" class="chart tall" :option="fleetOption" autoresize />
          <div v-else class="table-wrap">
            <table class="grid">
              <thead><tr><th>Номер</th><th>Пробег, км</th><th>Движение</th><th>Холостой</th><th>Стоит</th><th>Загрузка</th></tr></thead>
              <tbody>
                <tr v-for="v in data.vehicles" :key="v.vehicle_id">
                  <td class="mono">{{ v.plate }}</td><td class="mono">{{ v.mileage_km }}</td>
                  <td class="mono">{{ v.moving_min }} мин</td><td class="mono">{{ v.idle_min }} мин</td>
                  <td class="mono">{{ v.stopped_min }} мин</td><td class="mono">{{ v.utilization_pct }} %</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel box">
          <h2>Проходы в здания по СКУД</h2>
          <VChart class="chart" :option="buildingOption" autoresize />
          <small class="muted">Сейчас внутри:
            <span v-for="b in data.buildings" :key="b.building_id" class="mono people-now">{{ objects.buildings.get(b.building_id)?.name ?? b.building_id }} — {{ b.people_now }}</span>
          </small>
        </section>

        <section class="panel box">
          <h2>Прогноз рисков: климат</h2>
          <div v-if="!risks.length" class="empty-state">Все датчики климата стабильны: выхода за пороги в ближайшие 12 ч не ожидается</div>
          <table v-else class="grid">
            <thead><tr><th>Риск</th><th>Датчик</th><th>Прогноз</th></tr></thead>
            <tbody>
              <tr v-for="p in risks" :key="p.sensor_id" class="clickable" @click="router.push({ name: 'map', query: { sensor: p.sensor_id } })">
                <td><span class="badge" :class="RISK[p.risk][1]">{{ RISK[p.risk][0] }}</span></td>
                <td>{{ p.name }}</td>
                <td class="muted">{{ p.summary }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="panel box">
          <h2>Тревоги за период</h2>
          <div v-if="!data.alerts.opened" class="empty-state">Тревог не было</div>
          <template v-else>
            <VChart class="chart short" :option="severityOption" autoresize />
            <table class="grid">
              <thead><tr><th>Правило</th><th>Сработало</th></tr></thead>
              <tbody>
                <tr v-for="r in data.alerts.top_rules" :key="r.rule_id">
                  <td>{{ ruleNames.get(r.rule_id) ?? r.rule_id }}</td><td class="mono">{{ r.count }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.seg { display: flex; gap: 2px; }
.seg button.on { border-color: var(--accent); color: var(--accent); }
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 10px;
}
.tile { padding: 12px 14px; display: grid; gap: 4px; font-size: 12px; }
.tile .value { font-size: 24px; color: var(--text); }
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(520px, 100%), 1fr));
  gap: 12px;
}
.box { padding: 14px; display: grid; gap: 10px; align-content: start; }
.chart { height: 240px; }
.chart.tall { height: 380px; }
.chart.short { height: 130px; }
.table-wrap { max-height: 380px; overflow: auto; }
.people-now { margin-left: 10px; white-space: nowrap; }
tr.clickable { cursor: pointer; }
</style>
