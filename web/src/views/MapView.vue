<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { Alert, RoutePoint } from '@/api/types'
import AlertItem from '@/components/AlertItem.vue'
import CheatMenu from '@/components/CheatMenu.vue'
import LiveLayer from '@/components/LiveLayer.vue'
import ObjectCard from '@/components/ObjectCard.vue'
import PlanMap from '@/components/PlanMap.vue'
import RouteLayer from '@/components/RouteLayer.vue'
import { centroid, ll, type Selection } from '@/lib/plan'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const objects = useObjects()
const live = useLive()
const route = useRoute()
const router = useRouter()

const layers = reactive({ sensors: true, vehicles: true, people: true, alerts: true, roads: true, zones: true })
const floor = ref(1)
const selection = ref<Selection>(null)
const routePoints = ref<RoutePoint[] | null>(null)
const showCheat = ref(false)
const narrow = window.matchMedia('(max-width: 900px)')
const showLayers = ref(!narrow.matches) // on phones the layer panel would cover the map
const feedFilter = ref<'all' | 'critical' | 'open'>('all')
const planMap = ref<InstanceType<typeof PlanMap>>()

const LAYER_LABELS = [
  ['sensors', 'Датчики'],
  ['vehicles', 'Техника'],
  ['people', 'Люди'],
  ['alerts', 'Тревоги'],
  ['roads', 'Дороги'],
  ['zones', 'Геозоны'],
] as const

const feed = computed(() =>
  live.openAlerts.filter((a) =>
    feedFilter.value === 'critical' ? a.severity === 'critical' : feedFilter.value === 'open' ? a.status === 'open' : true,
  ),
)
const stats = computed(() => {
  const vehicles = [...live.vehicles.values()]
  return {
    moving: vehicles.filter((v) => v.status === 'moving').length,
    onSite: vehicles.filter((v) => v.geo.x >= 0).length,
    people: [...live.people.values()].reduce((s, n) => s + n, 0),
    offline: [...live.sensors.values()].filter((s) => s.status === 'offline').length,
  }
})

function positionOf(sel: NonNullable<Selection>): [number, number] | null {
  if (sel.kind === 'vehicle') {
    const v = live.vehicles.get(sel.id)
    return v ? [v.geo.x, v.geo.y] : null
  }
  if (sel.kind === 'sensor') {
    const s = objects.sensors.get(sel.id)
    if (s?.vehicle_id) return positionOf({ kind: 'vehicle', id: s.vehicle_id })
    return s?.geo ? [s.geo.x, s.geo.y] : null
  }
  const b = objects.buildings.get(sel.id)
  return b ? centroid(b.geometry.coordinates[0] as [number, number][]) : null
}

function focus(sel: NonNullable<Selection>) {
  selection.value = sel
  if (sel.kind === 'sensor') {
    const s = objects.sensors.get(sel.id)
    if (s?.floor && s.building_id) floor.value = s.floor
  }
  const pos = positionOf(sel)
  const map = planMap.value?.map
  if (pos && map) map.flyTo(ll(...pos), Math.max(map.getZoom(), 0.5), { duration: 0.6 })
}

function locate(a: Alert) {
  const sel: Selection = a.vehicle_id
    ? { kind: 'vehicle', id: a.vehicle_id }
    : a.sensor_id
      ? { kind: 'sensor', id: a.sensor_id }
      : a.building_id
        ? { kind: 'building', id: a.building_id }
        : null
  if (sel) focus(sel)
}

// deep link from the connectors page: /?sensor=clim-wh1-dock
watch(
  () => [route.query.sensor, planMap.value?.map] as const,
  ([id, map]) => {
    if (typeof id !== 'string' || !map || !objects.sensors.has(id)) return
    focus({ kind: 'sensor', id })
    router.replace({ query: {} })
  },
  { immediate: true },
)

// deep link from a toast or the journal: /?alert=123 (resolved alerts are fetched by id)
watch(
  () => [route.query.alert, planMap.value?.map] as const,
  async ([id, map]) => {
    if (!id || !map) return
    let a = live.alerts.get(Number(id))
    if (!a) {
      const r = await api.GET('/alerts/{alert_id}', { params: { path: { alert_id: Number(id) } } })
      a = r.data
    }
    if (a) locate(a)
    router.replace({ query: {} })
  },
  { immediate: true },
)
</script>

<template>
  <div class="map-view">
    <div class="map-area">
      <PlanMap
        v-if="objects.data"
        ref="planMap"
        :objects="objects.data"
        :floor="floor"
        :show-roads="layers.roads"
        :show-zones="layers.zones"
        :fit-top="150"
        @building="(id) => focus({ kind: 'building', id })"
        @background="selection = null"
      >
        <LiveLayer :floor="floor" :layers="layers" :selected="selection" @select="(s) => s && focus(s)" />
        <RouteLayer v-if="routePoints" :points="routePoints" />
      </PlanMap>
      <div v-else class="empty-state">Загрузка плана…</div>

      <div class="toolbar panel">
        <div class="stats">
          <div><b class="mono">{{ stats.moving }}</b>/{{ stats.onSite }}<small>в движении</small></div>
          <div><b class="mono">{{ stats.people }}</b><small>людей</small></div>
          <div :class="{ 'st-critical': live.criticalCount }"><b class="mono">{{ live.openAlerts.length }}</b><small>тревог</small></div>
          <div v-if="stats.offline" class="st-warning"><b class="mono">{{ stats.offline }}</b><small>нет связи</small></div>
        </div>
        <button class="small ghost layers-btn" :aria-expanded="showLayers" @click="showLayers = !showLayers">
          Слои и этажи {{ showLayers ? '▴' : '▾' }}
        </button>
        <div v-show="showLayers" class="toggles">
          <label v-for="[key, label] in LAYER_LABELS" :key="key" class="toggle">
            <input v-model="layers[key]" type="checkbox" />{{ label }}
          </label>
        </div>
        <div v-show="showLayers" class="row floors">
          <span class="muted">Этаж</span>
          <button v-for="f in objects.maxFloor" :key="f" class="small" :class="{ on: floor === f }" @click="floor = f">{{ f }}</button>
        </div>
        <div v-show="showLayers" class="legend">
          <span><i class="lg s-ok" />норма</span><span><i class="lg s-warning" />вне нормы</span>
          <span><i class="lg s-critical" />критично</span><span><i class="lg s-offline" />нет связи</span>
        </div>
      </div>

      <button class="cheat-btn" :class="{ on: showCheat }" @click="showCheat = !showCheat">▶ Сценарии</button>
      <CheatMenu v-if="showCheat" class="cheat-panel" @close="showCheat = false" />
    </div>

    <aside class="side">
      <ObjectCard
        v-if="selection"
        :selection="selection"
        @close="selection = null"
        @route="(p) => (routePoints = p)"
        @select="(s) => s && focus(s)"
      />
      <template v-else>
        <div class="row">
          <h2 class="grow">Активные тревоги</h2>
          <select v-model="feedFilter" aria-label="Фильтр тревог">
            <option value="all">все</option>
            <option value="open">неподтверждённые</option>
            <option value="critical">критичные</option>
          </select>
        </div>
        <div v-if="!feed.length" class="empty-state">Тревог нет — всё в норме</div>
        <TransitionGroup name="feed" tag="div" class="feed">
          <AlertItem v-for="a in feed" :key="a.id" :alert="a" compact @locate="locate" />
        </TransitionGroup>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.map-view {
  height: 100%;
  display: grid;
  grid-template-columns: 1fr 380px;
}
.map-area {
  position: relative;
  min-height: 0;
}
.side {
  border-left: 1px solid var(--border);
  background: var(--panel);
  padding: 14px;
  overflow: auto;
  display: grid;
  gap: 10px;
  align-content: start;
}
.feed {
  display: grid;
  gap: 8px;
}
.toolbar {
  position: absolute;
  top: 12px;
  left: 56px;
  z-index: 1000;
  padding: 10px 12px;
  display: grid;
  gap: 10px;
  max-width: calc(100% - 72px);
  background: #111a2cee;
}
.stats {
  display: flex;
  gap: 16px;
}
.layers-btn {
  justify-self: start;
  padding: 2px 6px;
  font-size: 12px;
  color: var(--text-muted);
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
.toggles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  cursor: pointer;
}
.floors button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 11px;
  color: var(--text-muted);
}
.lg {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  margin-right: 4px;
}
.lg.s-ok { background: var(--st-ok); }
.lg.s-warning { background: var(--st-warning); }
.lg.s-critical { background: var(--st-critical); }
.lg.s-offline { background: var(--st-offline); }
.cheat-btn {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 1000;
  background: #3b2a06;
  border-color: #a16207;
  color: #fde68a;
  font-weight: 600;
}
.cheat-btn.on {
  background: #a16207;
  color: #111;
}
.cheat-panel {
  position: absolute;
  left: 12px;
  bottom: 56px;
  width: min(360px, calc(100% - 24px));
  max-height: calc(100% - 200px);
  z-index: 1000;
  box-shadow: 0 10px 30px #000a;
}
.feed-enter-active,
.feed-leave-active {
  transition: all 0.3s;
}
.feed-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
.feed-leave-to {
  opacity: 0;
}
@media (max-width: 900px) {
  .map-view {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr 42%;
  }
  .side {
    border-left: none;
    border-top: 1px solid var(--border);
  }
  .toolbar {
    left: 50px;
    right: 8px;
    top: 8px;
    padding: 8px;
  }
  .legend {
    display: none;
  }
  .stats {
    gap: 12px;
  }
  .stats b {
    font-size: 15px;
  }
  .cheat-panel {
    top: 8px;
    bottom: 52px;
    max-height: none;
  }
}
</style>
