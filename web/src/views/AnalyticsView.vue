<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { Alert, Heatmap, Replay } from '@/api/types'
import HeatLayer from '@/components/HeatLayer.vue'
import PlanMap from '@/components/PlanMap.vue'
import ReplayLayer from '@/components/ReplayLayer.vue'
import { SEVERITY_LABEL, fromLocalInput, toLocalInput } from '@/lib/format'
import { toTrack, type Track } from '@/lib/replay'
import { useObjects } from '@/stores/objects'

const objects = useObjects()
const mode = ref<'replay' | 'heat'>('replay')
const DURATIONS = [
  { label: '15 мин', min: 15 },
  { label: '1 ч', min: 60 },
  { label: '3 ч', min: 180 },
  { label: '6 ч', min: 360 },
]
const duration = ref(15)
const fromInput = ref(toLocalInput(new Date(Date.now() - 15 * 60e3)))
const loading = ref(false)
const error = ref('')

// --- replay --------------------------------------------------------------------------------------
const replay = shallowRef<Replay | null>(null)
const tracks = shallowRef<Track[]>([])
const time = ref(0)
const playing = ref(false)
const speed = ref(30)
const SPEEDS = [1, 10, 30, 60, 120]
const span = computed(() => (replay.value ? [Date.parse(replay.value.period_from), Date.parse(replay.value.period_to)] : [0, 0]))

// --- heatmap -------------------------------------------------------------------------------------
const heat = shallowRef<Heatmap | null>(null)
const cell = ref(10)
const source = ref<'vehicles' | 'stops'>('vehicles')

function period() {
  const from = new Date(fromLocalInput(fromInput.value))
  return { from: from.toISOString(), to: new Date(from.getTime() + duration.value * 60e3).toISOString() }
}

function lastPeriod(min: number) {
  duration.value = min
  fromInput.value = toLocalInput(new Date(Date.now() - min * 60e3))
  load()
}

async function load() {
  loading.value = true
  error.value = ''
  playing.value = false
  try {
    if (mode.value === 'replay') {
      const step = Math.max(2, Math.round((duration.value * 60) / 900))
      const r = unwrap(await api.GET('/replay', { params: { query: { ...period(), step } } }))
      replay.value = r
      tracks.value = r.tracks.map((t) => toTrack(t.vehicle_id, t.points))
      // start where data starts: an empty head of the window (before recording began) is just a blank map
      const first = Math.min(...tracks.value.filter((t) => t.t.length).map((t) => t.t[0]))
      time.value = Number.isFinite(first) ? Math.max(Date.parse(r.period_from), first) : Date.parse(r.period_from)
    } else {
      heat.value = unwrap(await api.GET('/heatmap', { params: { query: { ...period(), cell: cell.value, source: source.value } } }))
    }
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}
watch(mode, load)
watch([cell, source], () => mode.value === 'heat' && load())
load()

let raf = 0
let last = 0
function frame(now: number) {
  if (!playing.value) return
  if (last) time.value = Math.min(span.value[1], time.value + (now - last) * speed.value)
  last = now
  if (time.value >= span.value[1]) playing.value = false
  else raf = requestAnimationFrame(frame)
}
watch(playing, (on) => {
  cancelAnimationFrame(raf)
  last = 0
  if (on) {
    if (time.value >= span.value[1]) time.value = span.value[0]
    raf = requestAnimationFrame(frame)
  }
})
onBeforeUnmount(() => cancelAnimationFrame(raf))

const clock = computed(() => (time.value ? new Date(time.value).toLocaleTimeString('ru-RU') : '—'))
const ticks = computed(() =>
  (replay.value?.alerts ?? []).map((a: Alert) => ({
    alert: a,
    left: ((Date.parse(a.opened_at) - span.value[0]) / Math.max(1, span.value[1] - span.value[0])) * 100,
  })),
)
const visibleAlerts = computed(() => (replay.value?.alerts ?? []).filter((a) => Date.parse(a.opened_at) <= time.value).slice(-6).reverse())
</script>

<template>
  <div class="analytics">
    <div class="controls panel">
      <div class="seg">
        <button class="small" :class="{ on: mode === 'replay' }" @click="mode = 'replay'">Replay</button>
        <button class="small" :class="{ on: mode === 'heat' }" @click="mode = 'heat'">Тепловая карта</button>
      </div>
      <label class="field inline">с<input v-model="fromInput" type="datetime-local" /></label>
      <select v-model.number="duration" aria-label="Длительность">
        <option v-for="d in DURATIONS" :key="d.min" :value="d.min">{{ d.label }}</option>
      </select>
      <button class="small primary" :disabled="loading" @click="load">{{ loading ? 'Загрузка…' : 'Показать' }}</button>
      <span class="muted">Последние:</span>
      <button v-for="d in DURATIONS" :key="'l' + d.min" class="small ghost" @click="lastPeriod(d.min)">{{ d.label }}</button>
      <template v-if="mode === 'heat'">
        <select v-model="source" aria-label="Что считать">
          <option value="vehicles">все позиции техники</option>
          <option value="stops">где техника стоит</option>
        </select>
        <select v-model.number="cell" aria-label="Ячейка">
          <option v-for="c in [5, 10, 20, 40]" :key="c" :value="c">ячейка {{ c }} м</option>
        </select>
      </template>
    </div>
    <div v-if="error" class="panel empty-state">{{ error }}</div>

    <div class="map-wrap">
      <PlanMap v-if="objects.data" :objects="objects.data" :floor="1">
        <ReplayLayer v-if="mode === 'replay'" :tracks="tracks" :time="time" />
        <HeatLayer v-if="mode === 'heat' && heat" :heat="heat" />
      </PlanMap>

      <div v-if="mode === 'replay' && replay" class="feed panel">
        <h3>Тревоги к {{ clock }}</h3>
        <div v-if="!visibleAlerts.length" class="muted">—</div>
        <div v-for="a in visibleAlerts" :key="a.id" class="row ev">
          <span class="badge" :class="`sev-${a.severity}`">{{ SEVERITY_LABEL[a.severity] }}</span>
          <span class="grow">{{ a.title }}</span>
        </div>
      </div>
      <div v-if="mode === 'heat' && heat" class="legend panel">
        <span class="muted">меньше</span><i class="ramp" /><span class="muted">больше</span>
        <small class="muted">макс {{ heat.max_count }} замеров в ячейке</small>
      </div>
    </div>

    <div v-if="mode === 'replay' && replay" class="timeline panel">
      <button class="primary" :aria-label="playing ? 'Пауза' : 'Воспроизвести'" @click="playing = !playing">{{ playing ? '❚❚' : '▶' }}</button>
      <span class="mono clock">{{ clock }}</span>
      <div class="track">
        <input v-model.number="time" type="range" :min="span[0]" :max="span[1]" step="1000" aria-label="Время" />
        <button v-for="t in ticks" :key="t.alert.id" class="tick" :class="`sev-${t.alert.severity}`" :style="{ left: `${t.left}%` }"
                :title="t.alert.title" @click="time = Date.parse(t.alert.opened_at)" />
      </div>
      <select v-model.number="speed" aria-label="Скорость">
        <option v-for="s in SPEEDS" :key="s" :value="s">×{{ s }}</option>
      </select>
      <span class="muted">{{ tracks.length }} машин · {{ replay.alerts.length }} тревог</span>
    </div>
  </div>
</template>

<style scoped>
.analytics { height: 100%; display: flex; flex-direction: column; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 0; border-width: 0 0 1px; }
.field.inline { grid-template-columns: auto 1fr; align-items: center; }
.seg { display: flex; gap: 2px; }
.seg button.on { border-color: var(--accent); color: var(--accent); }
.map-wrap { position: relative; flex: 1; min-height: 0; }
.feed { position: absolute; right: 12px; top: 12px; z-index: 1000; width: min(320px, 60%); padding: 10px; display: grid; gap: 6px; font-size: 12px; background: #111a2cee; }
.ev { gap: 6px; }
.legend { position: absolute; right: 12px; bottom: 12px; z-index: 1000; padding: 8px 10px; display: flex; align-items: center; gap: 8px; font-size: 12px; }
.ramp { display: inline-block; width: 120px; height: 10px; border-radius: 3px; background: linear-gradient(90deg, #3987e514, #3987e5e6); }
.timeline { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 0; border-width: 1px 0 0; }
.clock { font-size: 15px; min-width: 70px; }
.track { position: relative; flex: 1; display: flex; align-items: center; }
.track input { width: 100%; accent-color: var(--accent); }
.tick { position: absolute; top: -8px; width: 4px; height: 8px; padding: 0; border: none; border-radius: 1px; background: currentColor; transform: translateX(-2px); }
</style>
