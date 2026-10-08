<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ApiError, api, errorText, unwrap } from '@/api/client'
import type { ObjectsResponse } from '@/api/types'
import EditorLayer from '@/components/EditorLayer.vue'
import PlanMap from '@/components/PlanMap.vue'
import SensorEditor from '@/components/SensorEditor.vue'
import {
  PLACEABLE, ROOM_TYPES, diff, locate, newRoom, newSensor, relocateSensors, round, toObjects, validate,
  type Feature, type Layout, type Mode, type PlaceableType, type Pt,
} from '@/lib/editor'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

const objects = useObjects()
const toasts = useToasts()

const working = ref<Layout | null>(null)
const original = ref('') // JSON of the last saved version
const version = ref(0)
const base = shallowRef<ObjectsResponse | null>(null) // static plan for the renderer (buildings/roads don't change here)
const undo = ref<string[]>([])
const mode = ref<Mode>('select')
const floor = ref(1)
const selectedId = ref<string | null>(null)
const showCoverage = ref(true)
const saving = ref(false)
const problems = ref<string[]>([])
const conflict = ref(false)
const loadError = ref('')

const selected = computed(() => working.value?.features.find((f) => f.id === selectedId.value) ?? null)
const dirty = computed(() => !!working.value && JSON.stringify(working.value) !== original.value)
const changes = computed(() => (working.value && original.value ? diff(JSON.parse(original.value), working.value) : null))
const maxFloor = computed(() => Math.max(1, ...(base.value?.buildings ?? []).map((b) => b.floors)))
const registered = computed(() => selected.value?.properties.kind === 'sensor' && objects.sensors.get(selected.value.id))
const savedIds = computed(() => new Set(original.value ? (JSON.parse(original.value) as Layout).features.map((f) => f.id) : []))
const savedOnPlan = computed(() => !!selected.value && savedIds.value.has(selected.value.id))

async function load() {
  loadError.value = ''
  try {
    await objects.load()
    const doc = unwrap(await api.GET('/layout'))
    working.value = doc.geojson as unknown as Layout
    original.value = JSON.stringify(doc.geojson)
    version.value = doc.version
    base.value = toObjects(working.value, objects.data!, objects.data!.sensor_types)
    undo.value = []
    selectedId.value = null
    conflict.value = false
    problems.value = []
  } catch (e) {
    loadError.value = errorText(e)
  }
}

function snapshot() {
  undo.value = [...undo.value.slice(-49), JSON.stringify(working.value)]
}

function undoLast() {
  const prev = undo.value.pop()
  if (prev) {
    working.value = JSON.parse(prev)
    if (!working.value!.features.some((f) => f.id === selectedId.value)) selectedId.value = null
  }
}

const taken = () => new Set([...working.value!.features.map((f) => f.id), ...objects.sensors.keys()])
const typeName = (t: string) => PLACEABLE.find((p) => p.type === t)?.label ?? t

function place(p: Pt) {
  const l = working.value!
  if (p[0] < l.metadata.extent[0] || p[0] > l.metadata.extent[2] || p[1] < l.metadata.extent[1] || p[1] > l.metadata.extent[3]) {
    toasts.push({ kind: 'warning', title: 'За пределами площадки' })
    return
  }
  snapshot()
  const f = newSensor(l, mode.value as PlaceableType, p, floor.value, taken(), typeName(mode.value))
  l.features.push(f)
  selectedId.value = f.id
  mode.value = 'select'
}

function move(id: string, p: Pt) {
  const f = working.value!.features.find((x) => x.id === id)
  if (!f) return
  snapshot()
  f.geometry.coordinates = [round(p[0]), round(p[1])] as Pt
  Object.assign(f.properties, locate(working.value!, p, Number(f.properties.floor ?? floor.value)))
}

function drawRoom(a: Pt, b: Pt) {
  const room = newRoom(working.value!, a, b, floor.value, taken())
  if (typeof room === 'string') {
    toasts.push({ kind: 'warning', title: 'Помещение не создано', text: room })
    return
  }
  snapshot()
  working.value!.features.push(room)
  relocateSensors(working.value!, room)
  selectedId.value = room.id
  mode.value = 'select'
}

function remove(f: Feature) {
  snapshot()
  working.value!.features = working.value!.features.filter((x) => x.id !== f.id)
  if (f.properties.kind === 'room') relocateSensors(working.value!)
  selectedId.value = null
}

/** A new (never saved) sensor may still be renamed: keep feature id, property id and selection in step. */
function renameSelected(e: Event) {
  const f = selected.value
  const next = (e.target as HTMLInputElement).value.trim()
  if (!f || next === f.id) return
  if (taken().has(next)) {
    toasts.push({ kind: 'warning', title: `id ${next} уже занят` })
    ;(e.target as HTMLInputElement).value = f.id
    return
  }
  snapshot()
  f.id = next
  f.properties.id = next
  selectedId.value = next
}

function edited() {
  // properties edited in the side panel: one undo step per field change is enough
  snapshot()
}

function discard() {
  if (!confirm('Отменить все несохранённые изменения плана?')) return
  working.value = JSON.parse(original.value)
  undo.value = []
  selectedId.value = null
}

async function save() {
  problems.value = validate(working.value!)
  if (problems.value.length) return
  saving.value = true
  conflict.value = false
  try {
    const doc = unwrap(await api.POST('/layout', { body: { base_version: version.value, geojson: working.value as never } }))
    version.value = doc.version
    original.value = JSON.stringify(doc.geojson)
    working.value = doc.geojson as unknown as Layout
    undo.value = []
    await objects.load(true)
    toasts.push({ kind: 'success', title: `План сохранён (версия ${doc.version})`,
      text: 'Новые датчики появятся на карте; данные от климата и движения пойдут в течение минуты' })
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) conflict.value = true
    else if (e instanceof ApiError && e.status === 422 && Array.isArray(e.detail)) problems.value = e.detail.map(String)
    else problems.value = [errorText(e)]
  } finally {
    saving.value = false
  }
}

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault()
    undoLast()
  } else if (e.key === 'Escape') {
    mode.value = 'select'
  } else if ((e.key === 'Delete' || e.key === 'Backspace') && selected.value && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) {
    remove(selected.value)
  }
}
const beforeUnload = (e: BeforeUnloadEvent) => {
  if (dirty.value) e.preventDefault()
}
onMounted(() => {
  load()
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', beforeUnload)
})
onBeforeRouteLeave(() => !dirty.value || confirm('В плане есть несохранённые изменения. Уйти без сохранения?'))

const hint = computed(() =>
  mode.value === 'room' ? 'Протяните прямоугольник внутри здания' :
  mode.value !== 'select' ? `Кликните по плану, чтобы поставить: ${typeName(mode.value)}` :
  'Выбор: клик — свойства, перетаскивание датчика — перенос, Delete — удалить, Ctrl+Z — отменить',
)
</script>

<template>
  <div class="editor">
    <div class="map-area">
      <PlanMap v-if="base && working" :objects="base" :floor="floor" :fit-top="70" :clickable-buildings="false">
        <EditorLayer :layout="working" :floor="floor" :mode="mode" :selected="selectedId" :show-coverage="showCoverage"
                     @select="(id) => (selectedId = id)" @place="place" @move="move" @room="drawRoom" />
      </PlanMap>
      <div v-else class="empty-state">{{ loadError || 'Загрузка плана…' }}</div>

      <div class="palette panel">
        <button class="small" :class="{ on: mode === 'select' }" title="Esc" @click="mode = 'select'">↖ Выбор</button>
        <button v-for="p in PLACEABLE" :key="p.type" class="small" :class="{ on: mode === p.type }" @click="mode = p.type">+ {{ p.label }}</button>
        <button class="small" :class="{ on: mode === 'room' }" @click="mode = 'room'">▭ Помещение</button>
        <span class="sep" />
        <span class="muted">Этаж</span>
        <button v-for="f in maxFloor" :key="f" class="small" :class="{ on: floor === f }" @click="floor = f">{{ f }}</button>
        <label class="row cov"><input v-model="showCoverage" type="checkbox" />зоны покрытия</label>
      </div>
      <div class="hint muted">{{ hint }}</div>
    </div>

    <aside class="side">
      <div class="row">
        <h2 class="grow">Редактор плана</h2>
        <span class="muted mono">v{{ version }}</span>
      </div>

      <div v-if="changes && dirty" class="panel changes">
        <div>Датчики: <b class="st-ok">+{{ changes.sensors.added }}</b> · изменено {{ changes.sensors.changed }} ·
          <b class="st-critical">−{{ changes.sensors.removed }}</b></div>
        <div>Помещения: <b class="st-ok">+{{ changes.rooms.added }}</b> · изменено {{ changes.rooms.changed }} ·
          <b class="st-critical">−{{ changes.rooms.removed }}</b></div>
        <div class="row">
          <button class="primary" :disabled="saving" @click="save">{{ saving ? 'Сохранение…' : 'Сохранить план' }}</button>
          <button class="ghost" :disabled="!undo.length" title="Ctrl+Z" @click="undoLast">↶ Отменить</button>
          <button class="ghost" @click="discard">Сбросить</button>
        </div>
        <small class="muted">Убранные с плана датчики отключаются (история сохраняется); новые получают типовые пороги.</small>
      </div>
      <div v-else class="muted">Изменений нет. Выберите инструмент слева сверху.</div>

      <div v-if="conflict" class="panel error-box">
        <b>План уже сохранил кто-то другой.</b>
        <div>Ваши изменения основаны на версии {{ version }}. Загрузите свежий план и повторите правки.</div>
        <button class="small" @click="load">Загрузить свежий план (изменения пропадут)</button>
      </div>
      <div v-if="problems.length" class="panel error-box">
        <b>Нельзя сохранить:</b>
        <ul><li v-for="p in problems" :key="p">{{ p }}</li></ul>
      </div>

      <!-- selected sensor -->
      <section v-if="selected && selected.properties.kind === 'sensor'" class="panel box">
        <div class="row"><h3 class="grow">Датчик</h3><button class="small danger" @click="remove(selected)">Убрать с плана</button></div>
        <label class="field">ID
          <input :value="selected.id" class="mono" :disabled="savedOnPlan" @change="renameSelected" />
        </label>
        <label class="field">Название<input v-model="selected.properties.name" @change="edited" /></label>
        <div class="kv">
          <span class="muted">Тип</span><span>{{ typeName(String(selected.properties.sensor_type)) }}</span>
          <span class="muted">Здание</span><span>{{ base?.buildings.find((b) => b.id === selected!.properties.building_id)?.name ?? 'улица' }}</span>
          <span class="muted">Зона</span><span class="mono">{{ selected.properties.zone_id ?? '—' }}</span>
          <span class="muted">Координаты</span><span class="mono">{{ (selected.geometry.coordinates as Pt).join(', ') }} м</span>
        </div>
        <label class="field">Радиус покрытия, м: {{ selected.properties.coverage_m ?? '—' }}
          <input v-model.number="selected.properties.coverage_m" type="range" min="1" max="60" @change="edited" />
        </label>
        <SensorEditor v-if="registered && savedOnPlan" :sensor="registered" :editable="true" @saved="() => {}" @close="selectedId = null" />
        <p v-else class="muted small-text">Номиналы и пороги можно будет задать после сохранения плана — датчик создастся с типовыми порогами.</p>
      </section>

      <!-- selected room -->
      <section v-else-if="selected && selected.properties.kind === 'room'" class="panel box">
        <div class="row"><h3 class="grow">Помещение</h3><button class="small danger" @click="remove(selected)">Удалить</button></div>
        <label class="field">Название<input v-model="selected.properties.name" @change="edited" /></label>
        <label class="field">Назначение
          <select v-model="selected.properties.room_type" @change="edited">
            <option v-for="(label, k) in ROOM_TYPES" :key="k" :value="k">{{ label }}</option>
          </select>
        </label>
        <div class="kv">
          <span class="muted">ID</span><span class="mono">{{ selected.id }}</span>
          <span class="muted">Этаж</span><span>{{ selected.properties.floor }}</span>
          <span class="muted">Датчиков внутри</span>
          <span>{{ working!.features.filter((f) => f.properties.zone_id === selected!.id).length }}</span>
        </div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.editor { height: 100%; display: grid; grid-template-columns: 1fr 400px; }
.map-area { position: relative; min-height: 0; }
.palette {
  position: absolute; top: 12px; left: 56px; right: 12px; z-index: 1000; padding: 8px; display: flex; flex-wrap: wrap;
  align-items: center; gap: 6px; background: #111a2cee;
}
.palette button.on { border-color: var(--accent); color: var(--accent); background: #0c2238; }
.sep { width: 1px; height: 20px; background: var(--border); margin: 0 4px; }
.cov { font-size: 12px; gap: 4px; margin-left: auto; }
.hint {
  position: absolute; left: 12px; bottom: 12px; z-index: 1000; padding: 6px 10px; border-radius: 6px;
  background: #0b1220d0; font-size: 12px;
}
.side { border-left: 1px solid var(--border); background: var(--panel); padding: 14px; overflow: auto; display: grid; gap: 12px; align-content: start; }
.changes { padding: 12px; display: grid; gap: 8px; font-size: 13px; }
.box { padding: 12px; display: grid; gap: 10px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12px; }
.error-box { padding: 12px; display: grid; gap: 6px; border-color: #7f1d1d; background: #2a0f14; font-size: 13px; }
.error-box ul { margin: 0; padding-left: 18px; }
.small-text { font-size: 12px; margin: 0; }
@media (max-width: 900px) {
  .editor { grid-template-columns: 1fr; grid-template-rows: 1fr 45%; }
  .side { border-left: none; border-top: 1px solid var(--border); }
}
</style>
