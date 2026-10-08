<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { SensorType } from '@/api/types'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

const emit = defineEmits<{ close: [] }>()
const objects = useObjects()
const toasts = useToasts()
const form = reactive({ id: '', type: 'climate' as SensorType, name: '', building_id: '', zone_id: '', floor: 1, x: 0, y: 0 })
const busy = ref(false)
const error = ref('')

const fixedTypes = computed(() => (objects.data?.sensor_types ?? []).filter((t) => !t.is_mobile))
const zones = computed(() => [
  ...(objects.data?.rooms ?? []).filter((r) => !form.building_id || r.building_id === form.building_id),
  ...(form.building_id ? [] : objects.data?.geozones.filter((z) => z.zone_type !== 'speed') ?? []),
])

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const s = unwrap(await api.POST('/sensors', {
      body: {
        id: form.id.trim(), type: form.type, name: form.name.trim(),
        building_id: form.building_id || null, zone_id: form.zone_id || null,
        floor: form.building_id ? form.floor : null, geo: { x: form.x, y: form.y, floor: form.building_id ? form.floor : null },
        thresholds: [], enabled: true,
      },
    }))
    objects.upsertSensor(s)
    toasts.push({ kind: 'success', title: 'Датчик добавлен', text: `${s.name}: типовые пороги, данные начнут приходить в течение минуты` })
    emit('close')
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="panel create" @submit.prevent="submit">
    <header class="row">
      <h2 class="grow">Новый датчик</h2>
      <button type="button" class="small ghost" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>
    <div class="cols">
      <label class="field">ID<input v-model="form.id" required pattern="[a-z0-9][a-z0-9_\-]*" placeholder="clim-wh1-gate" /></label>
      <label class="field">Тип
        <select v-model="form.type"><option v-for="t in fixedTypes" :key="t.id" :value="t.id">{{ t.name }}</option></select>
      </label>
    </div>
    <label class="field">Название<input v-model="form.name" required /></label>
    <div class="cols">
      <label class="field">Здание
        <select v-model="form.building_id" @change="form.zone_id = ''">
          <option value="">— на улице —</option>
          <option v-for="b in objects.data?.buildings ?? []" :key="b.id" :value="b.id">{{ b.name }}</option>
        </select>
      </label>
      <label class="field">Зона
        <select v-model="form.zone_id">
          <option value="">—</option>
          <option v-for="z in zones" :key="z.id" :value="z.id">{{ z.name }}</option>
        </select>
      </label>
    </div>
    <div class="cols three">
      <label class="field">X, м<input v-model.number="form.x" type="number" step="0.5" required /></label>
      <label class="field">Y, м<input v-model.number="form.y" type="number" step="0.5" required /></label>
      <label class="field">Этаж<input v-model.number="form.floor" type="number" min="1" :disabled="!form.building_id" /></label>
    </div>
    <div v-if="error" class="error">{{ error }}</div>
    <div class="row"><span class="spacer" /><button class="primary" type="submit" :disabled="busy">Добавить</button></div>
  </form>
</template>

<style scoped>
.create { padding: 14px; display: grid; gap: 10px; align-content: start; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cols.three { grid-template-columns: 1fr 1fr 1fr; }
.error { color: #fecaca; background: #3b1219; border: 1px solid #7f1d1d; padding: 8px; border-radius: 6px; }
</style>
