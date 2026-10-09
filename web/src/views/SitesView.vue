<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import KpiCard from '@/components/KpiCard.vue'
import { SEVERITY_LABEL, SITE_TYPE_LABEL, fmtHM } from '@/lib/format'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

/** Objects: every site with its buildings, sensors, people, vehicles and alarms at a glance. */
const objects = useObjects()
const live = useLive()
const trips = useTrips()
const router = useRouter()

const RANK = { ok: 0, info: 0, warning: 1, critical: 2 } as const
const sensorStatus = (id: string) =>
  live.alertedObjects.get(id) === 'critical' ? 'critical' : live.sensors.get(id)?.status ?? 'offline'

const cards = computed(() =>
  (objects.data?.sites ?? []).map((s) => {
    const buildings = (objects.data?.buildings ?? []).filter((b) => b.site_id === s.id)
    const sensors = (objects.data?.sensors ?? []).filter((x) => x.enabled && !x.vehicle_id && objects.siteOfSensor(x.id) === s.id)
    const counts = { ok: 0, warning: 0, critical: 0, offline: 0 }
    for (const x of sensors) counts[sensorStatus(x.id) as keyof typeof counts]++
    const ids = new Set([...buildings.map((b) => b.id), ...sensors.map((x) => x.id)])
    const vehiclesHere = [...live.vehicles.values()].filter((v) => objects.siteAt(v.geo.x, v.geo.y) === s.id)
    for (const v of vehiclesHere) ids.add(v.vehicle_id)
    const alerts = live.openAlerts.filter((a) => ids.has(a.building_id ?? '') || ids.has(a.sensor_id ?? '') || ids.has(a.vehicle_id ?? ''))
    const worst = alerts.reduce<'ok' | 'warning' | 'critical'>(
      (w, a) => (RANK[a.severity] > RANK[w] ? (a.severity as 'warning' | 'critical') : w), 'ok')
    const roomsOf = (bid: string) => new Set((objects.data?.rooms ?? []).filter((r) => r.building_id === bid).map((r) => r.id))
    const peopleIn = (bid: string) => {
      const rooms = roomsOf(bid)
      let n = 0
      for (const [zone, c] of live.people) if (zone === bid || rooms.has(zone)) n += c
      return n
    }
    return {
      site: s,
      worst,
      alerts,
      counts,
      sensors: sensors.length,
      people: buildings.reduce((n, b) => n + peopleIn(b.id), 0),
      vehicles: vehiclesHere.length,
      inbound: trips.enRoute.filter((t) => t.destination_site_id === s.id),
      buildings: buildings.map((b) => ({
        b,
        people: peopleIn(b.id),
        sev: live.alertedObjects.get(b.id) ?? (sensors.some((x) => x.building_id === b.id && sensorStatus(x.id) === 'critical') ? 'critical' : null),
        sensors: sensors.filter((x) => x.building_id === b.id).length,
      })),
    }
  }),
)
const totals = computed(() => {
  const c = cards.value
  const sensors = c.reduce((n, x) => n + x.sensors, 0)
  const ok = c.reduce((n, x) => n + x.counts.ok, 0)
  return {
    sites: c.length,
    okPct: sensors ? Math.round((100 * ok) / sensors) : 0,
    ok, sensors,
    critical: live.criticalCount,
    alerts: live.openAlerts.length,
    people: c.reduce((n, x) => n + x.people, 0),
    enRoute: trips.enRoute.length,
  }
})
const TONE = { ok: 'st-ok', warning: 'st-warning', critical: 'st-critical' } as const
const BADGE = { ok: 'Норма', warning: 'Внимание', critical: 'Авария' } as const
const open = (query: Record<string, string>) => router.push({ path: '/', query })
</script>

<template>
  <div class="page">
    <header class="row"><h1 class="grow">Объекты предприятия</h1></header>
    <div class="kpis">
      <KpiCard label="Площадок" :value="totals.sites" sub="завод, РЦ, холодильный склад" />
      <KpiCard label="Датчики в норме" :value="totals.okPct" unit="%" :sub="`${totals.ok} из ${totals.sensors}`" :tone="totals.okPct > 90 ? 'ok' : 'warning'" />
      <KpiCard label="Активные тревоги" :value="totals.alerts" :sub="`критических: ${totals.critical}`" :tone="totals.critical ? 'critical' : totals.alerts ? 'warning' : 'ok'" />
      <KpiCard label="Людей на объектах" :value="totals.people" sub="по проходам СКУД" />
      <KpiCard label="Рейсов в пути" :value="totals.enRoute" sub="между площадками" />
    </div>

    <div class="cards">
      <section v-for="c in cards" :key="c.site.id" class="panel site" :class="`w-${c.worst}`">
        <div class="row">
          <div class="grow">
            <h2>{{ c.site.name }}</h2>
            <small class="muted">{{ SITE_TYPE_LABEL[c.site.site_type] }}{{ c.site.address ? ` · ${c.site.address}` : '' }}</small>
          </div>
          <span class="badge" :class="TONE[c.worst]">{{ BADGE[c.worst] }}</span>
        </div>

        <div class="stats">
          <div><b class="mono">{{ c.buildings.length }}</b><small>зданий</small></div>
          <div><b class="mono">{{ c.sensors }}</b><small>датчиков</small></div>
          <div><b class="mono">{{ c.people }}</b><small>людей</small></div>
          <div><b class="mono">{{ c.vehicles }}</b><small>техники</small></div>
          <div><b class="mono">{{ c.inbound.length }}</b><small>едут сюда</small></div>
        </div>
        <div class="bar" :title="`норма ${c.counts.ok}, внимание ${c.counts.warning}, авария ${c.counts.critical}, нет связи ${c.counts.offline}`">
          <i v-for="(n, k) in c.counts" :key="k" :class="`b-${k}`" :style="{ flex: n }" />
        </div>

        <div class="buildings">
          <button v-for="x in c.buildings" :key="x.b.id" class="bld" @click="open({ building: x.b.id })">
            <span class="dot" :class="x.sev ? `st-${x.sev}` : 'st-ok'">●</span>
            <span class="grow">{{ x.b.name }}</span>
            <small class="muted mono">👤 {{ x.people }} · {{ x.sensors }} дат.</small>
          </button>
        </div>

        <div v-if="c.inbound.length" class="inbound">
          <small class="muted">Едут сюда:</small>
          <span v-for="t in c.inbound" :key="t.id" class="chip mono" @click="open({ vehicle: t.vehicle_id })">{{ t.plate }} ≈ {{ fmtHM(t.eta) }}</span>
        </div>

        <div v-if="c.alerts.length" class="alerts">
          <div v-for="a in c.alerts.slice(0, 3)" :key="a.id" class="al">
            <span class="badge" :class="`sev-${a.severity}`">{{ SEVERITY_LABEL[a.severity] }}</span>
            <span class="grow">{{ a.title }}</span>
          </div>
          <small v-if="c.alerts.length > 3" class="muted">и ещё {{ c.alerts.length - 3 }}</small>
        </div>

        <button class="small open" @click="open({ site: c.site.id })">Открыть на карте</button>
      </section>
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
.cards {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
}
.site {
  display: grid;
  gap: 10px;
  padding: 14px;
  align-content: start;
  border-top: 3px solid var(--st-ok);
}
.site.w-warning {
  border-top-color: var(--st-warning);
}
.site.w-critical {
  border-top-color: var(--st-critical);
}
.stats {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.stats div {
  display: grid;
  line-height: 1.1;
}
.stats b {
  font-size: 18px;
}
.stats small {
  color: var(--text-muted);
  font-size: 11px;
}
.bar {
  display: flex;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--panel-2);
}
.b-ok { background: var(--st-ok); }
.b-warning { background: var(--st-warning); }
.b-critical { background: var(--st-critical); }
.b-offline { background: var(--st-offline); }
.buildings {
  display: grid;
  gap: 4px;
}
.bld {
  display: flex;
  gap: 8px;
  align-items: center;
  text-align: left;
  font-size: 12px;
  padding: 6px 8px;
  background: var(--bg);
  border-color: transparent;
  white-space: normal;
}
.dot {
  font-size: 10px;
}
.inbound {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 99px;
  border: 1px solid var(--border);
  cursor: pointer;
}
.alerts {
  display: grid;
  gap: 4px;
  font-size: 12px;
}
.al {
  display: flex;
  gap: 6px;
  align-items: center;
}
.open {
  justify-self: start;
}
</style>
