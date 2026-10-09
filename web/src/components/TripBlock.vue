<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, unwrap } from '@/api/client'
import type { Trip } from '@/api/types'
import { TRIP_STATUS_LABEL, fmtHM, fmtKm } from '@/lib/format'
import { progress, type Pt } from '@/lib/route'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

/** The vehicle's current trip by waybill: where from, where to, what it carries, how far along. */
const props = defineProps<{ vehicleId: string }>()
const emit = defineEmits<{ trip: [trip: Trip | null]; site: [siteId: string] }>()

const objects = useObjects()
const live = useLive()
const trip = ref<Trip | null>(null)
const loaded = ref(false)
let timer = 0

async function load() {
  try {
    trip.value = unwrap(await api.GET('/vehicles/{vehicle_id}/trip', { params: { path: { vehicle_id: props.vehicleId } } }))
  } catch {
    trip.value = null
  }
  loaded.value = true
  emit('trip', trip.value)
}
watch(
  () => props.vehicleId,
  () => {
    loaded.value = false
    trip.value = null
    void load()
  },
  { immediate: true },
)
timer = window.setInterval(load, 15_000)
onBeforeUnmount(() => window.clearInterval(timer))

const siteName = (id: string) => objects.data?.sites.find((s) => s.id === id)?.name ?? id
const route = computed(() => objects.data?.roads.find((r) => r.id === trip.value?.route_id))
const done = computed(() => {
  const t = trip.value
  const v = live.vehicles.get(props.vehicleId)
  const r = route.value
  if (!t || !r || !v) return null
  const line = r.geometry.coordinates as Pt[]
  const p = progress(line, [v.geo.x, v.geo.y], r.connects?.[0] !== t.origin_site_id)
  if (t.status === 'loading' || t.status === 'planned') return { pct: 0, left: p.total, total: p.total }
  if (t.status === 'unloading' || t.status === 'done') return { pct: 100, left: 0, total: p.total }
  return { pct: Math.round((100 * p.done) / p.total), left: p.left, total: p.total }
})
</script>

<template>
  <section v-if="trip" class="trip">
    <div class="row">
      <h3 class="grow">Рейс</h3>
      <span class="badge" :class="trip.status === 'en_route' ? 'st-ok' : 'st-info'">{{ TRIP_STATUS_LABEL[trip.status] }}</span>
    </div>
    <div class="leg">
      <button class="link" @click="emit('site', trip.origin_site_id)">{{ siteName(trip.origin_site_id) }}</button>
      <span class="arrow">→</span>
      <button class="link" @click="emit('site', trip.destination_site_id)">{{ siteName(trip.destination_site_id) }}</button>
    </div>
    <div v-if="done" class="progress" :title="`${done.pct}%`">
      <i :style="{ width: `${done.pct}%` }" />
    </div>
    <div v-if="done" class="row small muted">
      <span class="grow">пройдено {{ fmtKm(done.total - done.left) }} из {{ fmtKm(done.total) }}</span>
      <span v-if="trip.status === 'en_route'">прибытие ≈ <b class="mono">{{ fmtHM(trip.eta) }}</b></span>
    </div>
    <dl class="values">
      <dt>Груз</dt><dd>{{ trip.cargo }}</dd>
      <dt>Вес</dt><dd class="mono">{{ trip.weight_t }} т{{ trip.pallets ? ` · ${trip.pallets} пал.` : '' }}</dd>
      <template v-if="trip.temperature_mode"><dt>Режим</dt><dd class="mono">{{ trip.temperature_mode }}</dd></template>
      <dt>Водитель</dt><dd>{{ trip.driver_name ?? '—' }}</dd>
      <dt>Путевой лист</dt><dd class="mono">{{ trip.id }}</dd>
      <dt>Выезд</dt><dd class="mono">{{ trip.departed_at ? fmtHM(trip.departed_at) : trip.planned_departure ? `план ${fmtHM(trip.planned_departure)}` : '—' }}</dd>
      <template v-if="trip.arrived_at"><dt>Прибыл</dt><dd class="mono">{{ fmtHM(trip.arrived_at) }}</dd></template>
    </dl>
  </section>
  <div v-else-if="loaded" class="muted small">Рейсов по путевым листам нет</div>
</template>

<style scoped>
.trip {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}
.leg {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.arrow {
  color: var(--accent);
}
button.link {
  background: none;
  border: none;
  padding: 0;
  color: var(--text);
  font: inherit;
  cursor: pointer;
  text-align: left;
}
button.link:hover {
  color: var(--accent);
}
.progress {
  height: 6px;
  border-radius: 3px;
  background: var(--panel-2);
  overflow: hidden;
}
.progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent-2), var(--accent));
  transition: width 1s linear;
}
.small {
  font-size: 12px;
}
.values {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
}
.values dt {
  color: var(--text-muted);
}
.values dd {
  margin: 0;
  text-align: right;
}
</style>
