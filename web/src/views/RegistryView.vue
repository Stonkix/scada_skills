<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { Sensor, WhitelistPage } from '@/api/types'
import BulkUpload from '@/components/BulkUpload.vue'
import SensorCreate from '@/components/SensorCreate.vue'
import SensorEditor from '@/components/SensorEditor.vue'
import { SENSOR_STATUS_LABEL, fmtDateTime } from '@/lib/format'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const auth = useAuth()
const live = useLive()
const objects = useObjects()

const tab = ref<'sensors' | 'whitelist'>('sensors')
const type = ref('')
const building = ref('')
const q = ref('')
const panel = ref<'edit' | 'create' | 'bulk' | null>(null)
const editing = ref<Sensor | null>(null)

const sensors = computed(() =>
  (objects.data?.sensors ?? []).filter(
    (s) =>
      (!type.value || s.type === type.value) &&
      (!building.value || s.building_id === building.value) &&
      (!q.value || `${s.id} ${s.name}`.toLowerCase().includes(q.value.toLowerCase())),
  ),
)

function place(s: Sensor): string {
  if (s.vehicle_id) return objects.vehicles.get(s.vehicle_id)?.plate ?? s.vehicle_id
  const zone = s.zone_id ? objects.zoneNames.get(s.zone_id) : null
  return zone ?? (s.building_id ? objects.buildings.get(s.building_id)?.name ?? s.building_id : 'улица')
}

function thresholdsText(s: Sensor): string {
  return s.thresholds
    .map((t) => {
      const unit = objects.metric(s.type, t.metric)?.unit ?? ''
      const name = objects.metric(s.type, t.metric)?.name ?? t.metric
      return `${name}: ${t.min ?? '…'}–${t.max ?? '…'}${unit ? ' ' + unit : ''}`
    })
    .join('; ')
}

function statusText(s: Sensor): string {
  if (!s.enabled) return 'выключен'
  if (s.vehicle_id) return live.vehicles.has(s.vehicle_id) ? 'на связи' : 'нет данных'
  const st = live.sensors.get(s.id)?.status
  return st ? SENSOR_STATUS_LABEL[st] : 'нет данных'
}

function open(s: Sensor) {
  editing.value = s
  panel.value = 'edit'
}

// --- whitelist -----------------------------------------------------------------------------------
const wl = ref<WhitelistPage | null>(null)
const wlKind = ref<'card' | 'plate'>('card')
const wlQuery = ref('')
const wlActive = ref<'' | 'true' | 'false'>('')
const wlError = ref('')

async function loadWhitelist() {
  try {
    wl.value = unwrap(await api.GET('/whitelist', {
      params: { query: { kind: wlKind.value, q: wlQuery.value || undefined, active: wlActive.value ? wlActive.value === 'true' : undefined, limit: 200 } },
    }))
    wlError.value = ''
  } catch (e) {
    wlError.value = errorText(e)
  }
}
watch([wlKind, wlQuery, wlActive, tab], () => tab.value === 'whitelist' && loadWhitelist())
</script>

<template>
  <div class="registry">
    <div class="page main">
      <div class="row wrap">
        <h1 class="grow">Реестр</h1>
        <div class="tabs">
          <button class="small" :class="{ on: tab === 'sensors' }" @click="tab = 'sensors'">Датчики</button>
          <button class="small" :class="{ on: tab === 'whitelist' }" @click="tab = 'whitelist'">Пропуска и номера</button>
        </div>
      </div>

      <template v-if="tab === 'sensors'">
        <div class="row wrap">
          <input v-model="q" placeholder="Поиск по id или названию" class="search" />
          <select v-model="type" aria-label="Тип">
            <option value="">все типы</option>
            <option v-for="t in objects.data?.sensor_types ?? []" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <select v-model="building" aria-label="Здание">
            <option value="">все здания</option>
            <option v-for="b in objects.data?.buildings ?? []" :key="b.id" :value="b.id">{{ b.name }}</option>
          </select>
          <span class="muted">{{ sensors.length }} шт.</span>
          <span class="spacer" />
          <template v-if="auth.can('sensors:edit')">
            <button class="small" @click="panel = 'create'">+ Датчик</button>
            <button class="small" @click="panel = 'bulk'">Загрузить CSV</button>
          </template>
        </div>
        <div class="panel table-wrap">
          <table class="grid">
            <thead>
              <tr><th>Статус</th><th>ID</th><th>Название</th><th>Тип</th><th>Где</th><th>Пороги</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in sensors" :key="s.id" :class="{ disabled: !s.enabled, sel: editing?.id === s.id && panel === 'edit' }" @click="open(s)">
                <td>
                  <span :class="`st-${live.sensors.get(s.id)?.status ?? (live.vehicles.has(s.vehicle_id ?? '') ? 'ok' : 'offline')}`">●</span>
                  <small class="muted"> {{ statusText(s) }}</small>
                </td>
                <td class="mono">{{ s.id }}</td>
                <td>{{ s.name }}</td>
                <td class="muted">{{ objects.types.get(s.type)?.name }}</td>
                <td>{{ place(s) }}</td>
                <td class="muted small-text">{{ thresholdsText(s) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else>
        <div class="row wrap">
          <select v-model="wlKind" aria-label="Вид"><option value="card">Пропуска</option><option value="plate">Гос. номера</option></select>
          <input v-model="wlQuery" placeholder="Номер содержит…" class="search" />
          <select v-model="wlActive" aria-label="Действие"><option value="">все</option><option value="true">действующие</option><option value="false">недействующие</option></select>
          <span class="muted">{{ wl?.total ?? 0 }} шт.</span>
          <span v-if="!auth.can('people:view_pii')" class="muted">· ФИО скрыты (нужна роль «Охрана»)</span>
        </div>
        <div v-if="wlError" class="panel empty-state">{{ wlError }}</div>
        <div class="panel table-wrap">
          <table class="grid">
            <thead><tr><th>Номер</th><th>{{ wlKind === 'card' ? 'Владелец' : 'Машина' }}</th><th>Организация</th><th>Доступ</th><th>Действует до</th></tr></thead>
            <tbody>
              <tr v-for="e in wl?.items ?? []" :key="e.id" :class="{ disabled: !e.active }">
                <td class="mono">{{ e.value }}</td>
                <td>{{ e.holder_name ?? '—' }}</td>
                <td class="muted">{{ e.holder_org ?? '—' }}</td>
                <td class="muted">{{ e.allowed_building_ids?.map((b) => objects.buildings.get(b)?.name ?? b).join(', ') ?? 'везде' }}{{ e.schedule_id ? ` · ${e.schedule_id}` : '' }}</td>
                <td :class="e.active ? 'muted' : 'st-critical'">{{ e.valid_to ? fmtDateTime(e.valid_to) : 'бессрочно' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>

    <aside v-if="panel && tab === 'sensors'" class="side">
      <SensorEditor v-if="panel === 'edit' && editing" :sensor="editing" :editable="auth.can('sensors:edit')"
                    @saved="(s) => (editing = s)" @close="panel = null" />
      <SensorCreate v-else-if="panel === 'create'" @close="panel = null" />
      <BulkUpload v-else-if="panel === 'bulk'" @close="panel = null" />
    </aside>
  </div>
</template>

<style scoped>
.registry {
  height: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
}
.main {
  min-width: 0;
}
.side {
  width: min(560px, 100vw);
  border-left: 1px solid var(--border);
  overflow: auto;
  padding: 12px;
  background: var(--bg);
}
.tabs { display: flex; gap: 2px; }
.tabs button.on { border-color: var(--accent); color: var(--accent); }
.search { width: 240px; }
.table-wrap { overflow: auto; max-height: calc(100vh - 200px); }
tbody tr { cursor: pointer; }
tr.disabled { opacity: 0.5; }
tr.sel { background: var(--panel-2); }
.small-text { font-size: 11px; }
@media (max-width: 900px) {
  .registry { grid-template-columns: 1fr; }
  .side { position: fixed; inset: 48px 0 0 0; width: 100%; z-index: 1500; }
}
</style>
