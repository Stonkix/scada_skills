<script setup lang="ts">
import 'maplibre-gl/dist/maplibre-gl.css'
import type maplibregl from 'maplibre-gl'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { Alert, Heatmap, Replay } from '@/api/types'
import { SEVERITY_LABEL, fromLocalInput, toLocalInput } from '@/lib/format'
import { lngLatRing, toLngLat } from '@/lib/geo'
import { createBaseMap, fc, type BaseMap } from '@/lib/mapbase'
import { positionAt, trail, toTrack, type Track } from '@/lib/replay'
import { useObjects } from '@/stores/objects'

/** Replay and heatmap on the same 3D region map as the live one. */
const objects = useObjects()
const container = ref<HTMLDivElement>()
const base = shallowRef<BaseMap | null>(null)
const site = ref('')
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
const cell = ref(250) // the whole region; a site switches to 20 m
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
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  base.value?.destroy()
})

// --- the map -------------------------------------------------------------------------------------
type Pt = [number, number]
const VEHICLE_COLOR = { moving: '#fbbf24', stopped: '#94a3b8' }
const TRAIL_MS = 180_000

watch(
  () => [objects.data, container.value] as const,
  ([o, el]) => {
    if (!o || !el || base.value) return
    createBaseMap(el, o, (b) => {
      const m = b.map
      for (const id of ['trails', 'replay-vehicles', 'heat']) m.addSource(id, { type: 'geojson', data: fc([]) })
      m.addLayer({ id: 'trails', type: 'line', source: 'trails', layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#f97316', 'line-width': 3, 'line-opacity': 0.75 } })
      m.addLayer({ id: 'replay-vehicles', type: 'symbol', source: 'replay-vehicles', maxzoom: 14,
        layout: { 'icon-image': 'arrow', 'icon-rotate': ['get', 'heading'], 'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true, 'icon-size': 0.55 },
        paint: { 'icon-color': ['get', 'color'], 'icon-halo-color': '#0b1120', 'icon-halo-width': 2 } })
      // density as 3D columns: taller and warmer where vehicles spent more time
      m.addLayer({ id: 'heat', type: 'fill-extrusion', source: 'heat',
        paint: {
          'fill-extrusion-height': ['get', 'h'],
          'fill-extrusion-color': ['interpolate', ['linear'], ['get', 'k'], 0, '#0ea5e9', 0.4, '#22c55e', 0.7, '#facc15', 1, '#ef4444'],
          'fill-extrusion-opacity': 0.85,
        } })
      base.value = b
      drawReplay()
      drawHeat()
    })
  },
  { immediate: true },
)

function setData(id: string, data: GeoJSON.FeatureCollection) {
  ;(base.value?.map.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(data)
}

function drawReplay() {
  const b = base.value
  const o = objects.data
  if (!b || !o) return
  const show = mode.value === 'replay'
  const poses = show
    ? tracks.value.flatMap((tr) => {
        const p = positionAt(tr, time.value)
        return p ? [{ id: tr.id, x: p.x, y: p.y, heading: p.heading, moving: p.speed > 1 }] : []
      })
    : []
  b.layer.setPoses(poses, objects.vehicles)
  setData('replay-vehicles', fc(poses.map((p) => ({
    type: 'Feature', properties: { heading: Number.isNaN(p.heading) ? 0 : p.heading, color: VEHICLE_COLOR[p.moving ? 'moving' : 'stopped'] },
    geometry: { type: 'Point', coordinates: toLngLat(o.georef, p.x, p.y) },
  }))))
  setData('trails', fc(show ? tracks.value.map((tr) => trail(tr, time.value, TRAIL_MS)).filter((t) => t.length > 1)
    .map((t) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: t.map(([x, y]) => toLngLat(o.georef, x, y)) } }))
    : []))
}

function drawHeat() {
  const o = objects.data
  const h = heat.value
  if (!base.value || !o) return
  if (mode.value !== 'heat' || !h) return setData('heat', fc([]))
  const half = h.cell_m / 2
  setData('heat', fc(h.cells.map((c) => {
    const ring: Pt[] = [[c.x - half, c.y - half], [c.x + half, c.y - half], [c.x + half, c.y + half], [c.x - half, c.y + half], [c.x - half, c.y - half]]
    // log scale: one busy parking must not flatten every highway cell into nothing
    const k = h.max_count ? Math.log1p(c.count) / Math.log1p(h.max_count) : 0
    // columns scale with the cell: visible both on one site (10 m) and over the whole region (250 m)
    return { type: 'Feature', properties: { k, h: k * Math.max(60, h.cell_m * 2), count: c.count },
      geometry: { type: 'Polygon', coordinates: [lngLatRing(o.georef, ring)] } }
  })))
}

watch([tracks, time, mode], drawReplay)
watch([heat, mode], drawHeat)
watch(site, (id) => {
  if (id) base.value?.fitSite(id)
  else base.value?.fitRegion(true)
  cell.value = id ? 20 : 250 // cells sized for what is on screen
})

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
      <select v-model="site" aria-label="Площадка">
        <option value="">весь регион</option>
        <option v-for="s in objects.data?.sites ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <template v-if="mode === 'heat'">
        <select v-model="source" aria-label="Что считать">
          <option value="vehicles">все позиции техники</option>
          <option value="stops">где техника стоит</option>
        </select>
        <select v-model.number="cell" aria-label="Ячейка">
          <option v-for="c in [10, 20, 50, 100, 250]" :key="c" :value="c">ячейка {{ c }} м</option>
        </select>
      </template>
    </div>
    <div v-if="error" class="panel empty-state">{{ error }}</div>

    <div class="map-wrap">
      <div ref="container" class="map" />

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
.map { position: absolute; inset: 0; background: var(--map-bg); }
.feed { position: absolute; right: 56px; top: 12px; z-index: 2; width: min(320px, 60%); padding: 10px; display: grid; gap: 6px; font-size: 12px; background: #111a2cee; }
.ev { gap: 6px; }
.legend { position: absolute; right: 12px; bottom: 12px; z-index: 2; padding: 8px 10px; display: flex; align-items: center; gap: 8px; font-size: 12px; }
.ramp { display: inline-block; width: 120px; height: 10px; border-radius: 3px; background: linear-gradient(90deg, #0ea5e9, #22c55e, #facc15, #ef4444); }
.timeline { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 0; border-width: 1px 0 0; }
.clock { font-size: 15px; min-width: 70px; }
.track { position: relative; flex: 1; display: flex; align-items: center; }
.track input { width: 100%; accent-color: var(--accent); }
.tick { position: absolute; top: -8px; width: 4px; height: 8px; padding: 0; border: none; border-radius: 1px; background: currentColor; transform: translateX(-2px); }
</style>
