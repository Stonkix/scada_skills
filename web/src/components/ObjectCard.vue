<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { RoutePoint } from '@/api/types'
import ForecastBlock from '@/components/ForecastBlock.vue'
import HistoryChart from '@/components/HistoryChart.vue'
import AlertItem from '@/components/AlertItem.vue'
import {
  SENSOR_STATUS_LABEL,
  VEHICLE_KIND_LABEL,
  VEHICLE_STATUS_LABEL,
  fmtAgo,
  fmtValue,
} from '@/lib/format'
import { useNow } from '@/lib/now'
import type { Selection } from '@/lib/plan'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const props = defineProps<{ selection: NonNullable<Selection> }>()
const emit = defineEmits<{ close: []; route: [points: RoutePoint[] | null]; select: [sel: Selection] }>()

const live = useLive()
const objects = useObjects()

// --- sensor ----------------------------------------------------------------------------------------
const sensor = computed(() => (props.selection.kind === 'sensor' ? objects.sensors.get(props.selection.id) : undefined))
const sensorLive = computed(() => (sensor.value ? live.sensors.get(sensor.value.id) : undefined))
const typeInfo = computed(() => (sensor.value ? objects.types.get(sensor.value.type) : undefined))
const chartMetrics = computed(() => (typeInfo.value?.metrics ?? []).filter((m) => m.kind === 'number'))
const metric = ref('')
watch(chartMetrics, (ms) => (metric.value = ms[0]?.key ?? ''), { immediate: true })
const metricInfo = computed(() => chartMetrics.value.find((m) => m.key === metric.value))
const threshold = computed(() => sensor.value?.thresholds.find((t) => t.metric === metric.value))
const forecastPoints = ref<{ ts: string; value: number }[]>([])
const forecastable = computed(() => sensor.value?.type === 'climate')

// --- vehicle ---------------------------------------------------------------------------------------
const vehicle = computed(() => (props.selection.kind === 'vehicle' ? objects.vehicles.get(props.selection.id) : undefined))
const vehicleLive = computed(() => (vehicle.value ? live.vehicles.get(vehicle.value.id) : undefined))
const routeMinutes = ref<number | null>(null)
const routeError = ref('')
async function showRoute(minutes: number | null) {
  routeMinutes.value = minutes
  routeError.value = ''
  if (minutes === null || !vehicle.value) return emit('route', null)
  try {
    const to = new Date()
    const from = new Date(to.getTime() - minutes * 60e3)
    const r = unwrap(
      await api.GET('/vehicles/{vehicle_id}/route', {
        params: { path: { vehicle_id: vehicle.value.id }, query: { from: from.toISOString(), to: to.toISOString() } },
      }),
    )
    emit('route', r.points)
  } catch (e) {
    routeError.value = errorText(e)
  }
}
watch(() => props.selection, () => {
  routeMinutes.value = null
  emit('route', null)
})

// --- building --------------------------------------------------------------------------------------
const building = computed(() => (props.selection.kind === 'building' ? objects.buildings.get(props.selection.id) : undefined))
const buildingSensors = computed(() =>
  building.value ? (objects.data?.sensors ?? []).filter((s) => s.building_id === building.value!.id && s.enabled) : [],
)

const relatedAlerts = computed(() =>
  live.openAlerts.filter(
    (a) => a.sensor_id === props.selection.id || a.vehicle_id === props.selection.id || a.building_id === props.selection.id,
  ),
)
const title = computed(() => sensor.value?.name ?? (vehicle.value ? `${vehicle.value.plate} · ${vehicle.value.model}` : building.value?.name ?? props.selection.id))
const now = useNow()
</script>

<template>
  <section class="card">
    <header class="row">
      <h2 class="grow">{{ title }}</h2>
      <button class="ghost small" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>

    <!-- sensor -->
    <template v-if="sensor">
      <div class="row wrap meta">
        <span class="muted mono">{{ sensor.id }}</span>
        <span class="muted">· {{ typeInfo?.name }}</span>
        <span v-if="sensor.zone_id" class="muted">· {{ objects.zoneNames.get(sensor.zone_id) ?? sensor.zone_id }}</span>
      </div>
      <div class="row">
        <span class="badge" :class="`st-${sensorLive?.status ?? 'offline'}`">
          {{ sensorLive ? SENSOR_STATUS_LABEL[sensorLive.status] : 'Нет данных' }}
        </span>
        <small class="muted">{{ sensorLive ? `обновлено ${fmtAgo(sensorLive.last_seen, now)}` : '' }}</small>
      </div>
      <dl v-if="sensorLive" class="values">
        <template v-for="m in typeInfo?.metrics ?? []" :key="m.key">
          <template v-if="sensorLive.values[m.key] !== undefined">
            <dt>{{ m.name }}</dt>
            <dd class="mono">{{ fmtValue(sensorLive.values[m.key], m.unit) }}</dd>
          </template>
        </template>
      </dl>
      <template v-if="chartMetrics.length">
        <div class="row">
          <h3 class="grow">История</h3>
          <select v-if="chartMetrics.length > 1" v-model="metric">
            <option v-for="m in chartMetrics" :key="m.key" :value="m.key">{{ m.name }}</option>
          </select>
        </div>
        <HistoryChart :sensor-id="sensor.id" :metric="metric" :unit="metricInfo?.unit" :threshold="threshold"
                      :forecast="forecastable ? forecastPoints : undefined" />
        <ForecastBlock v-if="forecastable" :sensor-id="sensor.id" :metric="metric" @forecast="(p) => (forecastPoints = p)" />
        <div v-if="threshold" class="muted small-text">
          Норма {{ threshold.min ?? '−∞' }} … {{ threshold.max ?? '+∞' }}{{ metricInfo?.unit ? ' ' + metricInfo.unit : '' }},
          критично вне {{ threshold.critical_min ?? '−∞' }} … {{ threshold.critical_max ?? '+∞' }} · порог v{{ threshold.version }}
        </div>
      </template>
    </template>

    <!-- vehicle -->
    <template v-else-if="vehicle">
      <div class="row wrap meta">
        <span class="muted">{{ VEHICLE_KIND_LABEL[vehicle.kind] }}</span>
        <span v-if="vehicle.carrier" class="muted">· {{ vehicle.carrier }}</span>
        <span class="muted mono">· {{ vehicle.sensor_id }}</span>
      </div>
      <template v-if="vehicleLive">
        <div class="row">
          <span class="badge" :class="vehicleLive.status === 'moving' ? 'st-ok' : 'st-offline'">{{ VEHICLE_STATUS_LABEL[vehicleLive.status] }}</span>
          <small class="muted">обновлено {{ fmtAgo(vehicleLive.last_seen, now) }}</small>
        </div>
        <dl class="values">
          <dt>Скорость</dt><dd class="mono">{{ fmtValue(vehicleLive.speed_kmh, 'км/ч') }}</dd>
          <dt>Курс</dt><dd class="mono">{{ fmtValue(vehicleLive.heading_deg, '°') }}</dd>
          <dt>Топливо</dt><dd class="mono" :class="{ 'st-warning': (vehicleLive.fuel_pct ?? 100) < 15 }">{{ fmtValue(vehicleLive.fuel_pct, '%') }}</dd>
          <dt>Двигатель</dt><dd>{{ vehicleLive.engine_on ? 'работает' : 'заглушен' }}</dd>
          <dt>Зона</dt><dd>{{ vehicleLive.zone_id ? objects.zoneNames.get(vehicleLive.zone_id) ?? vehicleLive.zone_id : vehicleLive.geo.x < 0 ? 'за территорией' : 'проезд' }}</dd>
        </dl>
      </template>
      <div v-else class="muted">Нет данных от трекера</div>
      <div class="row wrap">
        <span class="muted">Маршрут:</span>
        <button v-for="m in [15, 60]" :key="m" class="small" :class="{ on: routeMinutes === m }" @click="showRoute(m)">{{ m }} мин</button>
        <button v-if="routeMinutes" class="small ghost" @click="showRoute(null)">скрыть</button>
      </div>
      <div v-if="routeError" class="muted">{{ routeError }}</div>
      <ForecastBlock :sensor-id="vehicle.sensor_id" />
      <HistoryChart :sensor-id="vehicle.sensor_id" metric="speed_kmh" unit="км/ч"
                    :threshold="objects.sensors.get(vehicle.sensor_id)?.thresholds.find((t) => t.metric === 'speed_kmh')" />
    </template>

    <!-- building -->
    <template v-else-if="building">
      <div class="row wrap meta">
        <span class="muted">{{ building.floors }} эт.</span>
        <span class="muted">· людей внутри по СКУД: <b class="mono">{{ live.people.get(building.id) ?? 0 }}</b></span>
      </div>
      <table class="grid compact">
        <tbody>
          <tr v-for="s in buildingSensors" :key="s.id" class="clickable" @click="emit('select', { kind: 'sensor', id: s.id })">
            <td><span class="dot" :class="`st-${live.sensors.get(s.id)?.status ?? 'offline'}`">●</span> {{ s.name }}</td>
            <td class="muted">{{ s.floor ? `${s.floor} эт.` : '' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-if="relatedAlerts.length">
      <h3>Тревоги</h3>
      <AlertItem v-for="a in relatedAlerts" :key="a.id" :alert="a" compact />
    </template>
  </section>
</template>

<style scoped>
.card {
  display: grid;
  gap: 10px;
  align-content: start;
}
.meta {
  font-size: 12px;
  gap: 4px;
}
.values {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
  padding: 10px;
  background: var(--bg);
  border-radius: 6px;
}
.values dt {
  color: var(--text-muted);
}
.values dd {
  margin: 0;
  text-align: right;
}
.small-text {
  font-size: 11px;
}
button.on {
  border-color: var(--accent);
  color: var(--accent);
}
table.compact td {
  padding: 5px 6px;
  font-size: 12px;
}
tr.clickable {
  cursor: pointer;
}
.dot {
  font-size: 10px;
}
</style>
