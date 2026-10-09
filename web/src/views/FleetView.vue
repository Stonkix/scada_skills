<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { Trip, VehicleLive } from '@/api/types'
import KpiCard from '@/components/KpiCard.vue'
import { TRIP_STATUS_LABEL, VEHICLE_KIND_LABEL, VEHICLE_STATUS_LABEL, fmtAgo, fmtHM, fmtKm } from '@/lib/format'
import { useNow } from '@/lib/now'
import { progress, type Pt } from '@/lib/route'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

/** Transport and logistics: every vehicle with its trip, cargo, speed and fuel; click a row to follow it. */
const objects = useObjects()
const live = useLive()
const trips = useTrips()
const router = useRouter()
const now = useNow()
const site = ref('')
const kind = ref<'all' | 'truck' | 'other'>('all')

const siteName = (id: string | null | undefined) => (id ? objects.sites.get(id)?.name ?? id : '')

interface Row {
  id: string
  plate: string
  model: string
  carrier: string | null
  kind: string
  live?: VehicleLive
  trip?: Trip
  where: string
  siteId: string | null
  left: number | null
  pct: number | null
}

const rows = computed<Row[]>(() =>
  (objects.data?.vehicles ?? []).map((v) => {
    const l = live.vehicles.get(v.id)
    const trip = trips.byVehicle.get(v.id)
    const siteId = l ? objects.siteAt(l.geo.x, l.geo.y) : null
    let left: number | null = null
    let pct: number | null = null
    const road = trip && objects.data?.roads.find((r) => r.id === trip.route_id)
    if (trip?.status === 'en_route' && road && l) {
      const p = progress(road.geometry.coordinates as Pt[], [l.geo.x, l.geo.y], road.connects?.[0] !== trip.origin_site_id)
      left = p.left
      pct = Math.round((100 * p.done) / p.total)
    }
    const where = siteId ? siteName(siteId) : trip?.status === 'en_route' ? `в пути на «${siteName(trip.destination_site_id)}»` : 'вне площадок'
    return { id: v.id, plate: v.plate, model: v.model, carrier: v.carrier ?? null, kind: v.kind, live: l, trip, where, siteId, left, pct }
  }),
)
const shown = computed(() =>
  rows.value.filter(
    (r) =>
      (!site.value || r.siteId === site.value || r.trip?.destination_site_id === site.value || r.trip?.origin_site_id === site.value) &&
      (kind.value === 'all' || (kind.value === 'truck') === (r.kind === 'truck')),
  ),
)
const kpi = computed(() => {
  const r = shown.value
  const moving = r.filter((x) => x.live?.status === 'moving')
  const enRoute = r.filter((x) => x.trip?.status === 'en_route')
  return {
    total: r.length,
    enRoute: enRoute.length,
    speed: moving.length ? Math.round(moving.reduce((s, x) => s + (x.live?.speed_kmh ?? 0), 0) / moving.length) : 0,
    lowFuel: r.filter((x) => (x.live?.fuel_pct ?? 100) < 15).length,
    tonnes: enRoute.reduce((s, x) => s + (x.trip?.weight_t ?? 0), 0),
  }
})

const statusOf = (r: Row) => (live.alertedObjects.has(r.id) ? 'critical' : r.live?.status === 'moving' ? 'ok' : r.live?.status === 'offline' ? 'offline' : 'idle')
const STATUS_DOT: Record<string, string> = { ok: 'var(--st-ok)', idle: 'var(--st-info)', offline: 'var(--st-offline)', critical: 'var(--st-critical)' }
const follow = (id: string) => router.push({ path: '/', query: { vehicle: id } })
</script>

<template>
  <div class="page">
    <header class="row wrap">
      <h1 class="grow">Транспорт и логистика</h1>
      <div class="seg">
        <button class="small" :class="{ on: kind === 'all' }" @click="kind = 'all'">Вся техника</button>
        <button class="small" :class="{ on: kind === 'truck' }" @click="kind = 'truck'">Грузовики</button>
        <button class="small" :class="{ on: kind === 'other' }" @click="kind = 'other'">Погрузчики и служебные</button>
      </div>
      <select v-model="site" aria-label="Площадка">
        <option value="">Все площадки</option>
        <option v-for="s in objects.data?.sites ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
    </header>

    <div class="kpis">
      <KpiCard label="Всего техники" :value="kpi.total" sub="в выбранной области" />
      <KpiCard label="В рейсе" :value="kpi.enRoute" :sub="`из ${kpi.total}`" />
      <KpiCard label="Средняя скорость" :value="kpi.speed" unit="км/ч" sub="у едущих" />
      <KpiCard label="Груза в пути" :value="kpi.tonnes.toFixed(1)" unit="т" sub="по путевым листам" />
      <KpiCard label="Мало топлива" :value="kpi.lowFuel" sub="меньше 15 %" :tone="kpi.lowFuel ? 'critical' : 'ok'" />
    </div>

    <div class="panel table-wrap">
      <table class="grid">
        <thead>
          <tr>
            <th>Госномер</th><th>Водитель</th><th>Где</th><th>Рейс</th><th>Груз</th><th>Статус</th>
            <th class="num">Скорость</th><th>Топливо</th><th>Прибытие</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in shown" :key="r.id" class="clickable" title="Показать на карте и следить" @click="follow(r.id)">
            <td>
              <b class="mono">{{ r.plate }}</b>
              <div class="muted small">{{ VEHICLE_KIND_LABEL[r.kind as keyof typeof VEHICLE_KIND_LABEL] }} · {{ r.model }}{{ r.carrier ? ` · ${r.carrier}` : '' }}</div>
            </td>
            <td>{{ r.trip?.driver_name ?? '—' }}</td>
            <td>{{ r.where }}</td>
            <td>
              <template v-if="r.trip">
                {{ siteName(r.trip.origin_site_id) }} → {{ siteName(r.trip.destination_site_id) }}
                <div v-if="r.pct !== null" class="prog"><i :style="{ width: `${r.pct}%` }" /></div>
                <div v-if="r.left !== null" class="muted small">осталось {{ fmtKm(r.left) }}</div>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <template v-if="r.trip">
                {{ r.trip.cargo }}
                <div class="muted small">{{ r.trip.weight_t }} т{{ r.trip.temperature_mode ? ` · ${r.trip.temperature_mode}` : '' }}</div>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <i class="dot" :style="{ background: STATUS_DOT[statusOf(r)] }" />
              {{ r.trip ? TRIP_STATUS_LABEL[r.trip.status] : r.live ? VEHICLE_STATUS_LABEL[r.live.status] : 'нет данных' }}
              <div class="muted small">{{ fmtAgo(r.live?.last_seen, now) }}</div>
            </td>
            <td class="num mono">{{ r.live ? Math.round(r.live.speed_kmh) : '—' }} <small class="muted">км/ч</small></td>
            <td>
              <div class="fuel" :title="`${Math.round(r.live?.fuel_pct ?? 0)} %`">
                <i :style="{ width: `${r.live?.fuel_pct ?? 0}%`, background: (r.live?.fuel_pct ?? 100) < 15 ? 'var(--st-critical)' : 'var(--st-ok)' }" />
              </div>
              <small class="muted mono">{{ r.live?.fuel_pct != null ? Math.round(r.live.fuel_pct) + ' %' : '' }}</small>
            </td>
            <td class="mono">{{ r.trip?.status === 'en_route' ? fmtHM(r.trip.eta) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
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
.kpis {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
}
.seg {
  display: flex;
  gap: 2px;
}
.seg button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.table-wrap {
  overflow-x: auto;
  padding: 0;
}
table td {
  vertical-align: top;
  padding: 9px 10px;
}
tr.clickable {
  cursor: pointer;
}
tr.clickable:hover td {
  background: var(--panel-2);
}
.small {
  font-size: 11px;
}
.num {
  text-align: right;
  white-space: nowrap;
}
.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  margin-right: 4px;
}
.prog,
.fuel {
  height: 6px;
  border-radius: 3px;
  background: var(--panel-2);
  overflow: hidden;
  min-width: 70px;
  margin-top: 4px;
}
.prog i,
.fuel i {
  display: block;
  height: 100%;
  background: var(--accent);
}
</style>
