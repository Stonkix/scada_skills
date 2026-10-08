<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { adapters as loadAdapters, api, errorText, ingest, unwrap, type IngestReply } from '@/api/client'
import type { AdapterInfo, ApiKey, Endpoints, Rejection, SensorType } from '@/api/types'
import CodeBlock from '@/components/CodeBlock.vue'
import { fmtDateTime, fmtTime } from '@/lib/format'
import { REASON_TEXT, curlCommand, mqttCommand, pythonSnippet, samplePayload, schemaRows } from '@/lib/snippets'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'
import { useToasts } from '@/stores/toasts'

const auth = useAuth()
const objects = useObjects()
const live = useLive()
const toasts = useToasts()
const router = useRouter()

const catalog = ref<AdapterInfo[]>([])
const endpoints = ref<Endpoints | null>(null)
const error = ref('')
const selected = ref('')
const adapter = computed(() => catalog.value.find((a) => a.name === selected.value) ?? null)

// --- step 2: sensor ---------------------------------------------------------------------------
const sensorType = computed<SensorType | null>(
  () => adapter.value?.sensor_type ?? ((adapter.value?.example.type as SensorType | undefined) ?? null),
)
const candidates = computed(() =>
  (objects.data?.sensors ?? []).filter((s) => s.enabled && s.type === sensorType.value).sort((a, b) => a.id.localeCompare(b.id)),
)
const sensorId = ref('')

// --- step 3: key ------------------------------------------------------------------------------
const apiKey = ref('') // kept in memory only: never written to storage
const keys = ref<ApiKey[]>([])
const newKeyName = ref('')
const restrictType = ref(true)
const issued = ref<string | null>(null)

// --- step 4: test -----------------------------------------------------------------------------
const payloadText = ref('')
const payloadError = ref('')
const sending = ref(false)
const reply = ref<{ status: number; body: Partial<IngestReply> & { detail?: unknown } } | null>(null)
const tab = ref<'curl' | 'mqtt' | 'python'>('curl')

const rejections = ref<Rejection[]>([])

function resetPayload() {
  if (!adapter.value) return
  payloadText.value = JSON.stringify(samplePayload(adapter.value, sensorId.value || null), null, 2)
  payloadError.value = ''
}

watch(adapter, (a) => {
  reply.value = null
  sensorId.value = candidates.value.find((s) => s.id === a?.example[a.device_field])?.id ?? candidates.value[0]?.id ?? ''
  resetPayload()
})
watch(sensorId, resetPayload)

const payload = computed<unknown>(() => {
  try {
    return JSON.parse(payloadText.value)
  } catch {
    return null
  }
})
watch(payloadText, () => (payloadError.value = payload.value === null && payloadText.value ? 'Невалидный JSON' : ''))

const keyForSnippets = computed(() => apiKey.value || '<API_KEY>')
const snippet = computed(() => {
  const a = adapter.value
  const e = endpoints.value
  if (!a || !e || payload.value === null) return ''
  if (tab.value === 'curl') return curlCommand(e, a, keyForSnippets.value, payload.value)
  if (tab.value === 'mqtt') return mqttCommand(e, a, keyForSnippets.value, sensorId.value || '{device_id}', payload.value)
  return pythonSnippet(e, a, keyForSnippets.value, payload.value)
})

// Live confirmation of *our* message: the sensor's live state shows an event measured at exactly the time we
// sent. Just "updated recently" would prove nothing — the simulator feeds the same sensors every few seconds.
const sent = ref<{ sensor: string; ts: number } | null>(null)
const confirmed = ref<string | null>(null)
watchEffect(() => {
  if (!sent.value || confirmed.value) return
  const s = live.sensors.get(sent.value.sensor)
  const vehicleId = objects.sensors.get(sent.value.sensor)?.vehicle_id
  const seen = s?.last_seen ?? (vehicleId ? live.vehicles.get(vehicleId)?.last_seen : undefined)
  if (seen && Math.abs(Date.parse(seen) - sent.value.ts) < 1) confirmed.value = seen
})

function measuredAt(a: AdapterInfo, p: Record<string, unknown>): number {
  const v = p[a.ts_field]
  return a.ts_format === 'unix_ms' ? Number(v) : a.ts_format === 'unix_s' ? Number(v) * 1000 : Date.parse(String(v))
}

async function send() {
  if (!adapter.value || payload.value === null) return
  if (!apiKey.value) {
    toasts.push({ kind: 'warning', title: 'Нужен API-ключ', text: 'Выпустите ключ в шаге 3 или вставьте свой' })
    return
  }
  sending.value = true
  try {
    reply.value = await ingest(adapter.value.name, apiKey.value, payload.value)
    confirmed.value = null
    const p = payload.value as Record<string, unknown>
    sent.value = reply.value.body.accepted ? { sensor: String(p[adapter.value.device_field]), ts: measuredAt(adapter.value, p) } : null
    setTimeout(loadRejections, 300)
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Сервис коннекторов недоступен', text: errorText(e) })
  } finally {
    sending.value = false
  }
}

async function issueKey() {
  try {
    const k = unwrap(await api.POST('/connectors/keys', {
      body: {
        name: newKeyName.value.trim() || `${adapter.value?.title ?? 'Устройство'} — ${new Date().toLocaleDateString('ru-RU')}`,
        sensor_types: restrictType.value && sensorType.value ? [sensorType.value] : null,
        rate_limit_per_min: 6000,
      },
    }))
    issued.value = k.key
    apiKey.value = k.key
    newKeyName.value = ''
    await loadKeys()
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Ключ не выпущен', text: errorText(e) })
  }
}

async function revoke(k: ApiKey) {
  const simulator = k.name === 'simulator'
  const text = simulator
    ? 'Это ключ симулятора: после отзыва демо-предприятие перестанет присылать данные (до пересоздания стенда). Отозвать?'
    : `Отозвать ключ «${k.name}»? Устройства с ним перестанут передавать данные в течение 30 с.`
  if (!confirm(text)) return
  try {
    unwrap(await api.DELETE('/connectors/keys/{key_id}', { params: { path: { key_id: k.id } } }))
    await loadKeys()
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Не удалось отозвать', text: errorText(e) })
  }
}

async function loadKeys() {
  if (!auth.can('connectors:manage')) return
  keys.value = unwrap(await api.GET('/connectors/keys'))
}

async function loadRejections() {
  try {
    rejections.value = unwrap(await api.GET('/connectors/rejections', { params: { query: { limit: 15 } } }))
  } catch {
    /* shown as empty */
  }
}

let timer: ReturnType<typeof setInterval>
onMounted(async () => {
  try {
    const [cat, ep] = await Promise.all([loadAdapters<AdapterInfo[]>(), api.GET('/connectors/endpoints').then(unwrap)])
    catalog.value = cat
    endpoints.value = ep
    selected.value = cat.find((a) => a.name === 'climate')?.name ?? cat[0]?.name ?? ''
  } catch (e) {
    error.value = errorText(e)
  }
  await Promise.all([loadKeys(), loadRejections()])
  timer = setInterval(loadRejections, 5000)
})
onBeforeUnmount(() => clearInterval(timer))

function openOnMap() {
  router.push({ name: 'map', query: { sensor: sent.value?.sensor ?? sensorId.value } })
}
const sensorName = computed(() => objects.sensors.get(sensorId.value)?.name)
const typeName = (t: string | null) => (t ? objects.types.get(t as SensorType)?.name ?? t : 'любой тип (контракт v1)')
</script>

<template>
  <div class="page connectors">
    <header class="row wrap">
      <div class="grow">
        <h1>Подключить датчик за 5 минут</h1>
        <p class="muted lead">
          Любое устройство, умеющее HTTP или MQTT, подключается без доработки системы: выберите формат, привяжите датчик из реестра,
          получите ключ и отправьте первое показание.
        </p>
      </div>
      <div v-if="endpoints" class="panel endpoints mono">
        <div><span class="muted">HTTP</span> {{ endpoints.http_base }}/ingest/&lt;формат&gt;</div>
        <div><span class="muted">MQTT</span> {{ endpoints.mqtt_host }}:{{ endpoints.mqtt_port }} · sensors/&lt;формат&gt;/&lt;id&gt;</div>
        <div><span class="muted">Ключ</span> заголовок {{ endpoints.api_key_header }} / MQTT 5 user property {{ endpoints.mqtt_key_property }}</div>
      </div>
    </header>
    <div v-if="error" class="panel empty-state">{{ error }}</div>

    <div class="layout">
      <!-- step 1 -->
      <nav class="catalog">
        <h2><span class="step">1</span>Формат</h2>
        <button v-for="a in catalog" :key="a.name" class="adapter" :class="{ on: a.name === selected }" @click="selected = a.name">
          <b>{{ a.title }}</b>
          <small class="muted"><span class="mono">{{ a.name }}</span> · {{ typeName(a.sensor_type) }}</small>
        </button>
      </nav>

      <div v-if="adapter" class="steps">
        <section class="panel box">
          <h3>Что присылает устройство</h3>
          <table class="grid fields">
            <thead><tr><th>Поле</th><th>Тип</th><th>Описание</th></tr></thead>
            <tbody>
              <tr v-for="f in schemaRows(adapter)" :key="f.name">
                <td class="mono">{{ f.name }}<span v-if="f.required" class="req" title="обязательное">*</span>
                  <span v-if="f.name === adapter.device_field" class="badge tag">id датчика</span>
                  <span v-if="f.name === adapter.ts_field" class="badge tag">время</span>
                </td>
                <td class="mono muted">{{ f.type }}</td>
                <td class="muted">{{ f.description }}</td>
              </tr>
            </tbody>
          </table>
          <p class="muted note">Можно слать один объект или массив (пачку). Повторная доставка того же сообщения не задвоится: id события
            вычисляется из устройства и времени.</p>
        </section>

        <!-- step 2 -->
        <section class="panel box">
          <h2><span class="step">2</span>Датчик в реестре</h2>
          <p class="muted note">Поле <span class="mono">{{ adapter.device_field }}</span> = id датчика в реестре. Неизвестные датчики отклоняются.</p>
          <div class="row wrap">
            <select v-model="sensorId" class="grow" aria-label="Датчик">
              <option v-for="s in candidates" :key="s.id" :value="s.id">{{ s.id }} — {{ s.name }}</option>
            </select>
            <RouterLink to="/registry" class="btn small">+ Новый датчик в реестре</RouterLink>
          </div>
        </section>

        <!-- step 3 -->
        <section class="panel box">
          <h2><span class="step">3</span>API-ключ</h2>
          <div v-if="auth.can('connectors:manage')" class="row wrap">
            <input v-model="newKeyName" class="grow" placeholder="Кому ключ: «Шлюз климата, склад №2»" />
            <label class="row check"><input v-model="restrictType" type="checkbox" />только {{ typeName(sensorType) }}</label>
            <button class="primary small" @click="issueKey">Выпустить ключ</button>
          </div>
          <div v-if="issued" class="issued">
            <b>Ключ выпущен. Сохраните его сейчас — повторно его не показать.</b>
            <CodeBlock :code="issued" label="API-ключ" />
          </div>
          <label class="field">Ключ для проверки и примеров ниже (хранится только на этой странице)
            <input v-model="apiKey" class="mono" placeholder="sk_…" autocomplete="off" spellcheck="false" />
          </label>
        </section>

        <!-- step 4 -->
        <section class="panel box">
          <div class="row">
            <h2 class="grow"><span class="step">4</span>Первое показание</h2>
            <button class="small ghost" @click="resetPayload">⟳ Пример с текущим временем</button>
          </div>
          <textarea v-model="payloadText" class="mono payload" rows="9" spellcheck="false" aria-label="Сообщение" />
          <div v-if="payloadError" class="st-critical">{{ payloadError }}</div>
          <div class="row wrap">
            <button class="primary" :disabled="sending || payload === null" @click="send">
              {{ sending ? 'Отправка…' : `Отправить как устройство (POST /ingest/${adapter.name})` }}
            </button>
            <span class="muted">идёт через сервис коннекторов тем же путём, что и у настоящего устройства</span>
          </div>
          <div v-if="reply" class="reply" :class="reply.body.accepted ? 'ok' : 'bad'">
            <template v-if="reply.body.accepted">
              <b class="st-ok">✓ Принято: {{ reply.body.accepted }}</b>
              <span class="muted mono">event_id {{ reply.body.event_ids?.[0] }}</span>
            </template>
            <template v-else-if="reply.status === 401 || reply.status === 429 || reply.status === 404">
              <b class="st-critical">✗ {{ reply.status }}: {{ reply.body.detail }}</b>
            </template>
            <div v-for="r in reply.body.rejected ?? []" :key="r.index">
              <b class="st-critical">✗ {{ REASON_TEXT[r.reason] ?? r.reason }}</b>
              <span class="muted mono"> ({{ r.reason }})</span>
              <div class="muted mono detail">{{ typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail) }}</div>
            </div>
          </div>
        </section>

        <!-- step 5 -->
        <section class="panel box">
          <h2><span class="step">5</span>Данные в системе</h2>
          <div v-if="confirmed" class="row wrap">
            <span class="st-ok">✓ Ваше показание дошло до карты: {{ sensorName }}, измерено {{ fmtTime(confirmed) }}</span>
            <button class="small" @click="openOnMap">Показать на карте</button>
          </div>
          <p v-else-if="sent" class="muted note">Ждём, пока событие пройдёт worker и появится в live-состоянии…</p>
          <p v-else class="muted note">После отправки здесь появится подтверждение: событие прошло коннектор и worker, записалось в
            ClickHouse и обновило карту.</p>

          <h3>Подключение с устройства</h3>
          <div class="tabs">
            <button class="small" :class="{ on: tab === 'curl' }" @click="tab = 'curl'">HTTP · curl</button>
            <button class="small" :class="{ on: tab === 'mqtt' }" @click="tab = 'mqtt'">MQTT · mosquitto_pub</button>
            <button class="small" :class="{ on: tab === 'python' }" @click="tab = 'python'">Python</button>
          </div>
          <CodeBlock v-if="snippet" :code="snippet" :label="tab === 'mqtt' ? `топик ${adapter.mqtt_topic}` : adapter.http" />
        </section>
      </div>
    </div>

    <section v-if="auth.can('connectors:manage')" class="panel box">
      <h2>Ключи</h2>
      <table class="grid">
        <thead><tr><th>Название</th><th>Префикс</th><th>Типы датчиков</th><th>Лимит</th><th>Выпущен</th><th></th></tr></thead>
        <tbody>
          <tr v-for="k in keys" :key="k.id" :class="{ revoked: k.revoked_at }">
            <td>{{ k.name }}</td>
            <td class="mono">{{ k.key_prefix }}…</td>
            <td class="muted">{{ k.sensor_types?.map(typeName).join(', ') ?? 'любые' }}</td>
            <td class="mono muted">{{ k.rate_limit_per_min }}/мин</td>
            <td class="muted">{{ fmtDateTime(k.created_at) }}</td>
            <td>
              <span v-if="k.revoked_at" class="muted">отозван {{ fmtDateTime(k.revoked_at) }}</span>
              <button v-else class="small danger" @click="revoke(k)">Отозвать</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel box">
      <div class="row">
        <h2 class="grow">Последние отклонённые сообщения</h2>
        <small class="muted">обновляется каждые 5 с · stream:dlq</small>
      </div>
      <div v-if="!rejections.length" class="empty-state">Отклонённых сообщений нет</div>
      <table v-else class="grid">
        <thead><tr><th>Когда</th><th>Причина</th><th>Формат</th><th>Канал</th><th>Ключ</th><th>Подробности</th></tr></thead>
        <tbody>
          <tr v-for="r in rejections" :key="r.id">
            <td class="mono muted">{{ fmtTime(r.received_at) }}</td>
            <td><b>{{ REASON_TEXT[r.reason] ?? r.reason }}</b></td>
            <td class="mono">{{ r.adapter }}</td>
            <td class="muted">{{ r.source }}</td>
            <td class="muted">{{ r.api_key }}</td>
            <td class="muted mono detail" :title="r.raw ?? ''">{{ typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.lead { margin: 4px 0 0; max-width: 760px; }
.endpoints { padding: 10px 12px; display: grid; gap: 3px; font-size: 12px; }
.layout { display: grid; grid-template-columns: 260px 1fr; gap: 16px; align-items: start; }
.catalog { display: grid; gap: 6px; position: sticky; top: 0; }
.adapter { display: grid; justify-items: start; gap: 2px; text-align: left; white-space: normal; padding: 9px 11px; }
.adapter.on { border-color: var(--accent); background: #0c2238; }
.steps { display: grid; gap: 12px; min-width: 0; }
.box { padding: 14px; display: grid; gap: 10px; align-content: start; min-width: 0; }
.step {
  display: inline-grid; place-items: center; width: 22px; height: 22px; margin-right: 8px; border-radius: 50%;
  background: var(--accent-2); color: #04121f; font: 700 12px/1 var(--mono);
}
.fields td { padding: 5px 8px; font-size: 12px; }
.req { color: var(--st-critical); margin-left: 2px; }
.tag { margin-left: 6px; color: var(--accent); font-weight: 500; }
.note { margin: 0; font-size: 12px; }
.check { font-size: 12px; gap: 4px; }
.issued { display: grid; gap: 6px; padding: 10px; border: 1px solid #a16207; border-radius: 6px; background: #2a1d05; }
.payload { width: 100%; resize: vertical; font-size: 12px; line-height: 1.45; }
.reply { padding: 10px 12px; border-radius: 6px; display: grid; gap: 4px; border: 1px solid var(--border); }
.reply.ok { border-color: #166534; background: #0b2316; }
.reply.bad { border-color: #7f1d1d; background: #2a0f14; }
.detail { font-size: 11px; max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tabs { display: flex; gap: 2px; }
.tabs button.on { border-color: var(--accent); color: var(--accent); }
tr.revoked { opacity: 0.5; }
a.btn { text-decoration: none; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .catalog { position: static; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
}
</style>
