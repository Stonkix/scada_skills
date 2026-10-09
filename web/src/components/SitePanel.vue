<script setup lang="ts">
import { computed } from 'vue'
import AlertItem from '@/components/AlertItem.vue'
import { SITE_TYPE_LABEL, TRIP_STATUS_LABEL, VEHICLE_STATUS_LABEL, fmtHM, headline } from '@/lib/format'
import type { Selection } from '@/lib/plan'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

/** Site card: buildings, vehicles on site, trucks coming and going, outdoor sensors, alerts. */
const props = defineProps<{ siteId: string }>()
const emit = defineEmits<{ close: []; select: [sel: Selection] }>()

const objects = useObjects()
const live = useLive()
const trips = useTrips()

const site = computed(() => objects.data?.sites.find((s) => s.id === props.siteId))
const bounds = computed(() => {
  const ring = (site.value?.geometry.coordinates[0] ?? []) as [number, number][]
  const xs = ring.map((p) => p[0])
  const ys = ring.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
})
const inside = (x: number, y: number) => x >= bounds.value[0] && x <= bounds.value[2] && y >= bounds.value[1] && y <= bounds.value[3]
const buildings = computed(() => (objects.data?.buildings ?? []).filter((b) => b.site_id === props.siteId))
const outdoor = computed(() =>
  (objects.data?.sensors ?? []).filter((s) => s.enabled && !s.building_id && !s.vehicle_id && s.geo && inside(s.geo.x, s.geo.y)),
)
const vehicles = computed(() =>
  [...live.vehicles.values()].filter((v) => inside(v.geo.x, v.geo.y)).map((v) => ({ live: v, reg: objects.vehicles.get(v.vehicle_id) })),
)
const inbound = computed(() => trips.active.filter((t) => t.destination_site_id === props.siteId && t.status === 'en_route'))
const outbound = computed(() => trips.active.filter((t) => t.origin_site_id === props.siteId && t.status === 'loading'))
const siteName = (id: string) => objects.data?.sites.find((s) => s.id === id)?.name ?? id
const ids = computed(() => new Set([...buildings.value.map((b) => b.id), ...outdoor.value.map((s) => s.id)]))
const alerts = computed(() => live.openAlerts.filter((a) => ids.value.has(a.building_id ?? '') || ids.value.has(a.sensor_id ?? '')))
const people = (bid: string) => {
  const rooms = new Set((objects.data?.rooms ?? []).filter((r) => r.building_id === bid).map((r) => r.id))
  let n = 0
  for (const [zone, count] of live.people) if (zone === bid || rooms.has(zone)) n += count
  return n
}
</script>

<template>
  <section v-if="site" class="card">
    <header class="row">
      <div class="grow">
        <h2>{{ site.name }}</h2>
        <small class="muted">{{ SITE_TYPE_LABEL[site.site_type] }}{{ site.address ? ` · ${site.address}` : '' }}</small>
      </div>
      <button class="ghost small" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>

    <h3>Здания</h3>
    <button v-for="b in buildings" :key="b.id" class="item" @click="emit('select', { kind: 'building', id: b.id })">
      <span class="dot" :class="live.alertedObjects.get(b.id) ? `st-${live.alertedObjects.get(b.id)}` : 'st-ok'">●</span>
      <span class="grow">{{ b.name }}</span>
      <small class="muted mono">👤 {{ people(b.id) }}</small>
    </button>

    <template v-if="inbound.length || outbound.length">
      <h3>Рейсы</h3>
      <button v-for="t in inbound" :key="t.id" class="item" @click="emit('select', { kind: 'vehicle', id: t.vehicle_id })">
        <span class="mono">{{ t.plate }}</span>
        <span class="grow muted">← {{ siteName(t.origin_site_id) }} · {{ t.cargo }}</span>
        <small class="mono">≈ {{ fmtHM(t.eta) }}</small>
      </button>
      <button v-for="t in outbound" :key="t.id" class="item" @click="emit('select', { kind: 'vehicle', id: t.vehicle_id })">
        <span class="mono">{{ t.plate }}</span>
        <span class="grow muted">→ {{ siteName(t.destination_site_id) }} · {{ t.cargo }}</span>
        <small>{{ TRIP_STATUS_LABEL[t.status] }}</small>
      </button>
    </template>

    <template v-if="vehicles.length">
      <h3>Техника на площадке</h3>
      <button v-for="v in vehicles" :key="v.live.vehicle_id" class="item" @click="emit('select', { kind: 'vehicle', id: v.live.vehicle_id })">
        <span class="mono">{{ v.reg?.plate ?? v.live.vehicle_id }}</span>
        <span class="grow muted">{{ v.reg?.model }}</span>
        <small>{{ VEHICLE_STATUS_LABEL[v.live.status] }}</small>
      </button>
    </template>

    <template v-if="outdoor.length">
      <h3>Датчики на территории</h3>
      <button
        v-for="s in outdoor"
        :key="`${s.id}@${live.sensors.get(s.id)?.last_seen ?? ''}`"
        class="item flash"
        @click="emit('select', { kind: 'sensor', id: s.id })"
      >
        <span class="dot" :class="`st-${live.sensors.get(s.id)?.status ?? 'offline'}`">●</span>
        <span class="grow">{{ s.name }}</span>
        <small class="mono">{{ headline(s.type, live.sensors.get(s.id)?.values) }}</small>
      </button>
    </template>

    <template v-if="alerts.length">
      <h3>Тревоги</h3>
      <AlertItem v-for="a in alerts" :key="a.id" :alert="a" compact />
    </template>
  </section>
</template>

<style scoped>
.card {
  display: grid;
  gap: 6px;
  align-content: start;
}
h3 {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.item {
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: var(--bg);
  color: var(--text);
  font-size: 12px;
}
.item:hover {
  border-color: var(--border);
}
.dot {
  font-size: 10px;
}
.flash {
  animation: blink 0.9s ease-out;
}
@keyframes blink {
  from {
    background: #38bdf855;
  }
  to {
    background: var(--bg);
  }
}
</style>
