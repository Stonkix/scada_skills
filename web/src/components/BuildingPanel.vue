<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { Sensor } from '@/api/types'
import AlertItem from '@/components/AlertItem.vue'
import { SENSOR_STATUS_LABEL, fmtAgo, headline } from '@/lib/format'
import { useNow } from '@/lib/now'
import type { Selection } from '@/lib/plan'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

/** Building card: every sensor of the building with its live value, room by room, floor by floor. */
const props = defineProps<{ buildingId: string; floor: number; highlight?: string | null }>()
const emit = defineEmits<{ close: []; floor: [floor: number]; select: [sel: Selection] }>()

const objects = useObjects()
const live = useLive()
const auth = useAuth()
const now = useNow()

const building = computed(() => objects.buildings.get(props.buildingId))
const site = computed(() => objects.data?.sites.find((s) => s.id === building.value?.site_id))
const sensors = computed(() => (objects.data?.sensors ?? []).filter((s) => s.building_id === props.buildingId && s.enabled))
const rooms = computed(() => (objects.data?.rooms ?? []).filter((r) => r.building_id === props.buildingId))
const unplaced = computed(() => sensors.value.filter((s) => !s.geo))
/** No floor plan, or sensors without a place: the card works, the 3D view can't show them. */
const needsLayout = computed(() => !rooms.value.length || unplaced.value.length > 0)

const groups = computed(() => {
  const onFloor = sensors.value.filter((s) => s.geo && (s.floor ?? 1) === props.floor)
  const out: { id: string; name: string; sensors: Sensor[] }[] = rooms.value
    .filter((r) => r.floor === props.floor)
    .map((r) => ({ id: r.id, name: r.name, sensors: onFloor.filter((s) => s.zone_id === r.id) }))
  const inRooms = new Set(out.flatMap((g) => g.sensors.map((s) => s.id)))
  const rest = onFloor.filter((s) => !inRooms.has(s.id))
  if (rest.length) out.push({ id: '_building', name: 'Здание целиком', sensors: rest })
  if (unplaced.value.length) out.push({ id: '_unplaced', name: 'Без места на плане', sensors: unplaced.value })
  return out.filter((g) => g.sensors.length || g.id.startsWith('r-'))
})

const statusOf = (id: string) => (live.alertedObjects.get(id) === 'critical' ? 'critical' : live.sensors.get(id)?.status ?? 'offline')
const people = computed(() => {
  const ids = new Set([props.buildingId, ...rooms.value.map((r) => r.id)])
  let n = 0
  for (const [zone, count] of live.people) if (ids.has(zone)) n += count
  return n
})
const alerts = computed(() => live.openAlerts.filter((a) => a.building_id === props.buildingId))
const counts = computed(() => {
  const c = { ok: 0, warning: 0, critical: 0, offline: 0 }
  for (const s of sensors.value) c[statusOf(s.id) as keyof typeof c]++
  return c
})
</script>

<template>
  <section v-if="building" class="card">
    <header class="row">
      <div class="grow">
        <h2>{{ building.name }}</h2>
        <small class="muted">{{ site?.name }} · {{ building.floors }} эт. · людей внутри: <b class="mono">{{ people }}</b></small>
      </div>
      <button class="ghost small" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>

    <div v-if="needsLayout" class="hint">
      <b>Рекомендуем указать размещение датчиков на объекте</b>
      <span v-if="!rooms.length">У здания нет планировки: датчики работают, но на карте их не видно.</span>
      <span v-else>{{ unplaced.length }} датчик(а) без места на плане.</span>
      <RouterLink v-if="auth.can('layout:edit')" :to="{ path: '/editor', query: { building: building.id } }" class="btn small">
        Разместить в редакторе
      </RouterLink>
    </div>

    <div class="row wrap stats">
      <span class="st-ok">● {{ counts.ok }} норма</span>
      <span v-if="counts.warning" class="st-warning">● {{ counts.warning }} вне нормы</span>
      <span v-if="counts.critical" class="st-critical">● {{ counts.critical }} критично</span>
      <span v-if="counts.offline" class="muted">● {{ counts.offline }} нет связи</span>
    </div>

    <div v-if="building.floors > 1" class="row floors">
      <span class="muted">Этаж</span>
      <button v-for="f in building.floors" :key="f" class="small" :class="{ on: floor === f }" @click="emit('floor', f)">{{ f }}</button>
    </div>

    <div v-for="g in groups" :key="g.id" class="group">
      <h3>{{ g.name }}</h3>
      <div v-if="!g.sensors.length" class="muted small">датчиков нет</div>
      <!-- keyed by the last report: the row re-mounts and flashes on every event -->
      <button
        v-for="s in g.sensors"
        :key="`${s.id}@${live.sensors.get(s.id)?.last_seen ?? ''}`"
        class="srow"
        :class="{ on: highlight === s.id }"
        @click="emit('select', { kind: 'sensor', id: s.id })"
      >
        <span class="dot" :class="`st-${statusOf(s.id)}`" :title="SENSOR_STATUS_LABEL[statusOf(s.id) as keyof typeof SENSOR_STATUS_LABEL]">●</span>
        <span class="name">{{ s.name }}</span>
        <span class="val mono">{{ headline(s.type, live.sensors.get(s.id)?.values) }}</span>
        <small class="ago muted">{{ fmtAgo(live.sensors.get(s.id)?.last_seen, now) }}</small>
      </button>
    </div>

    <template v-if="alerts.length">
      <h3>Тревоги</h3>
      <AlertItem v-for="a in alerts" :key="a.id" :alert="a" compact />
    </template>
  </section>
</template>

<style scoped>
.card {
  display: grid;
  gap: 10px;
  align-content: start;
}
.hint {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #a16207;
  background: #3b2a0699;
  color: #fde68a;
  font-size: 12px;
}
.hint .btn {
  justify-self: start;
  margin-top: 4px;
}
.stats {
  gap: 10px;
  font-size: 12px;
}
.floors button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.group {
  display: grid;
  gap: 4px;
}
.group h3 {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 600;
}
.srow {
  white-space: normal;
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-rows: auto auto;
  gap: 0 8px;
  align-items: center;
  text-align: left;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: var(--bg);
  color: var(--text);
  animation: blink 0.9s ease-out;
}
.srow.on {
  border-color: var(--accent);
}
.srow .dot {
  grid-row: span 2;
  font-size: 10px;
}
.srow .name {
  font-size: 12px;
}
.srow .val {
  font-size: 12px;
  text-align: right;
}
.srow .ago {
  grid-column: 2 / 4;
  font-size: 10px;
}
.small {
  font-size: 12px;
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
