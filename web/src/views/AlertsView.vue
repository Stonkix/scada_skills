<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorText, unwrap } from '@/api/client'
import type { Alert } from '@/api/types'
import AlertItem from '@/components/AlertItem.vue'
import { ALERT_KIND_LABEL, SEVERITY_LABEL, STATUS_LABEL, fromLocalInput } from '@/lib/format'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

type Status = Alert['status']
type Kind = Alert['kind']

const objects = useObjects()
const live = useLive()
const router = useRouter()
const PAGE = 30

const filters = reactive({
  status: ['open', 'ack'] as Status[],
  severity: '' as '' | Alert['severity'],
  kind: '' as '' | Kind,
  building: '',
  from: '',
  to: '',
})
const page = ref(0)
const items = ref<Alert[]>([])
const total = ref(0)
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = unwrap(
      await api.GET('/alerts', {
        params: {
          query: {
            status: filters.status.length ? filters.status : undefined,
            severity: filters.severity ? [filters.severity] : undefined,
            kind: filters.kind ? [filters.kind] : undefined,
            building_id: filters.building || undefined,
            from: filters.from ? fromLocalInput(filters.from) : undefined,
            to: filters.to ? fromLocalInput(filters.to) : undefined,
            limit: PAGE,
            offset: page.value * PAGE,
          },
        },
      }),
    )
    items.value = r.items
    total.value = r.total
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

watch(filters, () => {
  page.value = 0
  load()
}, { deep: true })
watch(page, load)
load()

// keep the journal fresh when alerts change live
let pending: ReturnType<typeof setTimeout> | null = null
watch(() => live.alerts, () => {
  if (pending) return
  pending = setTimeout(() => {
    pending = null
    load()
  }, 1500)
})

function toggleStatus(s: Status) {
  filters.status = filters.status.includes(s) ? filters.status.filter((x) => x !== s) : [...filters.status, s]
}

function onChanged(a: Alert) {
  items.value = items.value.map((x) => (x.id === a.id ? a : x))
}
</script>

<template>
  <div class="page">
    <div class="row wrap">
      <h1 class="grow">Журнал тревог</h1>
      <span class="muted">{{ loading ? 'обновление…' : `${total} шт.` }}</span>
    </div>

    <div class="panel filters">
      <div class="row wrap">
        <button v-for="(label, s) in STATUS_LABEL" :key="s" class="small" :class="{ on: filters.status.includes(s) }"
                @click="toggleStatus(s)">{{ label }}</button>
      </div>
      <select v-model="filters.severity" aria-label="Критичность">
        <option value="">любая критичность</option>
        <option v-for="(label, s) in SEVERITY_LABEL" :key="s" :value="s">{{ label }}</option>
      </select>
      <select v-model="filters.kind" aria-label="Тип">
        <option value="">любой тип</option>
        <option v-for="(label, k) in ALERT_KIND_LABEL" :key="k" :value="k">{{ label }}</option>
      </select>
      <select v-model="filters.building" aria-label="Здание">
        <option value="">все здания</option>
        <option v-for="b in objects.data?.buildings ?? []" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
      <label class="field">с<input v-model="filters.from" type="datetime-local" /></label>
      <label class="field">по<input v-model="filters.to" type="datetime-local" /></label>
    </div>

    <div v-if="error" class="panel empty-state">{{ error }}</div>
    <div v-else-if="!items.length && !loading" class="panel empty-state">Ничего не найдено</div>
    <div class="list">
      <AlertItem v-for="a in items" :key="a.id" :alert="a"
                 @locate="router.push({ name: 'map', query: { alert: String(a.id) } })" @changed="onChanged" />
    </div>

    <div v-if="total > PAGE" class="row">
      <button class="small" :disabled="page === 0" @click="page--">← Назад</button>
      <span class="muted">{{ page * PAGE + 1 }}–{{ Math.min(total, (page + 1) * PAGE) }} из {{ total }}</span>
      <button class="small" :disabled="(page + 1) * PAGE >= total" @click="page++">Вперёд →</button>
    </div>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 10px;
  padding: 12px;
}
.filters .field {
  grid-template-columns: auto 1fr;
  align-items: center;
}
button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.list {
  display: grid;
  gap: 8px;
  max-width: 900px;
}
</style>
