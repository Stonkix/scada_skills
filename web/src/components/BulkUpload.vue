<script setup lang="ts">
import { ref } from 'vue'
import { API_BASE, ApiError, errorText, refreshTokens, tokens } from '@/api/client'
import type { BulkResult } from '@/api/types'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

const emit = defineEmits<{ close: [] }>()
const objects = useObjects()
const toasts = useToasts()
const file = ref<File | null>(null)
const result = ref<BulkResult | null>(null)
const busy = ref(false)
const error = ref('')

const TEMPLATE =
  'id,type,name,building_id,zone_id,floor,x,y\n' +
  'clim-wh1-new,climate,Климат: склад №1 у ворот,b-wh1,r-wh1-storage,1,240,340\n' +
  'mot-wh2-new,motion,Движение: склад №2 у рампы,b-wh2,r-wh2-dock,1,420,310\n'

/** multipart upload: openapi-fetch does not serialise files, so this one goes through fetch directly. */
async function send(dryRun: boolean): Promise<BulkResult> {
  const post = () => {
    const form = new FormData()
    form.append('file', file.value!)
    return fetch(`${API_BASE}/sensors/bulk?dry_run=${dryRun}`, {
      method: 'POST',
      body: form,
      headers: { Authorization: `Bearer ${tokens.get()?.access_token ?? ''}` },
    })
  }
  let res = await post()
  if (res.status === 401 && (await refreshTokens())) res = await post()
  const body = await res.json()
  if (!res.ok) throw new ApiError(res.status, body.detail)
  return body as BulkResult
}

async function check() {
  if (!file.value) return
  busy.value = true
  error.value = ''
  try {
    result.value = await send(true)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function apply() {
  busy.value = true
  try {
    const r = await send(false)
    result.value = r
    if (r.created) {
      toasts.push({ kind: 'success', title: `Добавлено датчиков: ${r.created}` })
      await objects.load(true)
      emit('close')
    }
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

function pick(e: Event) {
  file.value = (e.target as HTMLInputElement).files?.[0] ?? null
  result.value = null
  if (file.value) check()
}

function downloadTemplate() {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob(['﻿' + TEMPLATE], { type: 'text/csv' }))
  a.download = 'sensors-template.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <section class="panel bulk">
    <header class="row">
      <h2 class="grow">Загрузка списка датчиков</h2>
      <button class="small ghost" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>
    <p class="muted">CSV или JSON. Сначала файл проверяется без записи; добавление — всё или ничего.
      <a href="#" @click.prevent="downloadTemplate">Скачать шаблон CSV</a></p>
    <input type="file" accept=".csv,.json,text/csv,application/json" @change="pick" />
    <div v-if="busy" class="muted">Проверка…</div>
    <div v-if="error" class="error">{{ error }}</div>
    <template v-if="result">
      <div class="row">
        <span class="badge st-ok">готово к добавлению: {{ result.valid }}</span>
        <span v-if="result.errors.length" class="badge st-critical">ошибок: {{ result.errors.length }}</span>
        <span class="muted">строк: {{ result.total }}</span>
      </div>
      <table v-if="result.errors.length" class="grid">
        <thead><tr><th>Строка</th><th>Поле</th><th>Ошибка</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in result.errors" :key="i"><td class="mono">{{ e.row }}</td><td class="mono">{{ e.field ?? '—' }}</td><td>{{ e.message }}</td></tr>
        </tbody>
      </table>
      <div class="row">
        <span class="spacer" />
        <button class="primary" :disabled="busy || !!result.errors.length || !result.valid" @click="apply">
          Добавить {{ result.valid }}
        </button>
      </div>
      <p v-if="result.errors.length" class="muted">Исправьте ошибки в файле и выберите его снова.</p>
    </template>
  </section>
</template>

<style scoped>
.bulk { padding: 14px; display: grid; gap: 10px; align-content: start; }
.bulk p { margin: 0; font-size: 12px; }
.error { color: #fecaca; background: #3b1219; border: 1px solid #7f1d1d; padding: 8px; border-radius: 6px; }
</style>
