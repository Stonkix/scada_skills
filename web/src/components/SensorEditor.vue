<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { Sensor, Threshold } from '@/api/types'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ sensor: Sensor; editable: boolean }>()
const emit = defineEmits<{ saved: [sensor: Sensor]; close: [] }>()
const objects = useObjects()
const toasts = useToasts()

const name = ref('')
const enabled = ref(true)
const rows = ref<Threshold[]>([])
const busy = ref(false)
const error = ref('')

const metrics = computed(() => (objects.types.get(props.sensor.type)?.metrics ?? []).filter((m) => m.kind === 'number'))
const FIELDS = [
  ['nominal', 'Номинал'],
  ['min', 'Мин'],
  ['max', 'Макс'],
  ['critical_min', 'Крит. мин'],
  ['critical_max', 'Крит. макс'],
] as const

watch(
  () => props.sensor,
  (s) => {
    name.value = s.name
    enabled.value = s.enabled
    rows.value = s.thresholds.map((t) => ({ ...t }))
    error.value = ''
  },
  { immediate: true },
)

const unusedMetrics = computed(() => metrics.value.filter((m) => !rows.value.some((r) => r.metric === m.key)))

function addRow(metric: string) {
  rows.value.push({ metric, nominal: null, min: null, max: null, critical_min: null, critical_max: null, version: 1 })
}

function num(v: unknown): number | null {
  return v === '' || v == null ? null : Number(v)
}

function validate(): string {
  for (const r of rows.value) {
    const [min, max, cmin, cmax] = [r.min, r.max, r.critical_min, r.critical_max].map(num)
    if (min != null && max != null && min > max) return `${r.metric}: мин больше макс`
    if (cmin != null && min != null && cmin > min) return `${r.metric}: критический минимум выше нормы`
    if (cmax != null && max != null && cmax < max) return `${r.metric}: критический максимум ниже нормы`
  }
  return ''
}

async function save() {
  error.value = validate()
  if (error.value) return
  busy.value = true
  try {
    let s = props.sensor
    if (name.value !== s.name || enabled.value !== s.enabled) {
      s = unwrap(await api.PATCH('/sensors/{sensor_id}', {
        params: { path: { sensor_id: s.id } }, body: { name: name.value, enabled: enabled.value },
      }))
    }
    const changed = JSON.stringify(rows.value.map(({ version: _, ...t }) => t)) !==
      JSON.stringify(props.sensor.thresholds.map(({ version: _, ...t }) => t))
    if (changed) {
      const body = rows.value.map((r) => ({
        metric: r.metric, nominal: num(r.nominal), min: num(r.min), max: num(r.max),
        critical_min: num(r.critical_min), critical_max: num(r.critical_max), version: 1,
      }))
      s = unwrap(await api.PUT('/sensors/{sensor_id}/thresholds', { params: { path: { sensor_id: s.id } }, body }))
    }
    objects.upsertSensor(s)
    emit('saved', s)
    toasts.push({ kind: 'success', title: 'Сохранено', text: changed ? 'Новая версия порогов применится в течение 30 с' : s.name })
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="panel editor">
    <header class="row">
      <h2 class="grow">{{ sensor.id }}</h2>
      <button class="small ghost" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>
    <label class="field">Название<input v-model="name" :disabled="!editable" /></label>
    <label class="row toggle"><input v-model="enabled" type="checkbox" :disabled="!editable" />принимать данные</label>
    <h3>Пороги</h3>
    <div v-if="!metrics.length" class="muted">У этого типа нет числовых метрик</div>
    <table v-else class="grid th">
      <thead>
        <tr><th>Метрика</th><th v-for="[, l] in FIELDS" :key="l">{{ l }}</th><th>v</th><th v-if="editable"></th></tr>
      </thead>
      <tbody>
        <tr v-for="(r, i) in rows" :key="r.metric">
          <td>{{ metrics.find((m) => m.key === r.metric)?.name ?? r.metric }}</td>
          <td v-for="[f] in FIELDS" :key="f"><input v-model="r[f]" type="number" step="any" :disabled="!editable" /></td>
          <td class="muted mono">{{ r.version }}</td>
          <td v-if="editable"><button class="small ghost" title="Удалить порог" @click="rows.splice(i, 1)">✕</button></td>
        </tr>
      </tbody>
    </table>
    <div v-if="editable && unusedMetrics.length" class="row wrap">
      <span class="muted">Добавить порог:</span>
      <button v-for="m in unusedMetrics" :key="m.key" class="small" @click="addRow(m.key)">{{ m.name }}</button>
    </div>
    <p class="muted hint">Норма — между «мин» и «макс» (вне неё — предупреждение), критично — вне крит. границ. Сохранение создаёт новую версию: тревоги помнят, по какой версии сработали.</p>
    <div v-if="error" class="error">{{ error }}</div>
    <div v-if="editable" class="row">
      <span class="spacer" />
      <button class="primary" :disabled="busy" @click="save">{{ busy ? 'Сохранение…' : 'Сохранить' }}</button>
    </div>
  </section>
</template>

<style scoped>
.editor { padding: 14px; display: grid; gap: 10px; align-content: start; }
.th input { width: 72px; padding: 4px 6px; }
.th td, .th th { padding: 5px 4px; }
.toggle { font-size: 13px; }
.hint { font-size: 11px; margin: 0; }
.error { color: #fecaca; background: #3b1219; border: 1px solid #7f1d1d; padding: 8px; border-radius: 6px; }
</style>
