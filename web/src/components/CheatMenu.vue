<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { errorText, sim } from '@/api/client'
import { useToasts } from '@/stores/toasts'

interface Scenario {
  name: string
  title: string
  description: string
  steps: unknown[]
}
interface SimStatus {
  time_scale: number
  chaos: { on: boolean; every_s: number; recent: { at: number; what: string }[] }
  effects: { kind: string; target: string; left_s: number }[]
  runs: { id: string; scenario: string; step: number; done: boolean; error: string | null }[]
}

const emit = defineEmits<{ close: [] }>()
const toasts = useToasts()
const scenarios = ref<Scenario[]>([])
const status = ref<SimStatus | null>(null)
const error = ref('')
const busy = ref('')

const EFFECT_LABEL: Record<string, string> = {
  breakdown: 'поломка',
  breakdown_next: 'поломка при выезде на проезд',
  speed: 'превышение',
  climate_drift: 'нагрев',
  motion_alarm: 'движение',
  silence: 'нет связи',
  closed: 'здание закрыто',
}

async function refresh() {
  try {
    status.value = await sim<SimStatus>('/status')
    error.value = ''
  } catch (e) {
    error.value = `Симулятор недоступен: ${errorText(e)}`
  }
}

async function run(name: string) {
  busy.value = name
  try {
    await sim(`/scenario/${name}`, { method: 'POST' })
    toasts.push({ kind: 'success', title: 'Сценарий запущен', text: scenarios.value.find((s) => s.name === name)?.title })
    await refresh()
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Сценарий не запущен', text: errorText(e) })
  } finally {
    busy.value = ''
  }
}

const SPEEDS = [1, 5, 10, 30] as const
const CHAOS_EVERY = [10, 20, 40, 60] as const
const chaosEvery = ref(20)

/** Random incidents all over the region while switched on: every one raises an alarm at once. */
async function setChaos(on: boolean) {
  try {
    await sim('/chaos', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on, every_s: chaosEvery.value }) })
    toasts.push({ kind: on ? 'warning' : 'info', title: on ? `Случайные аварии: примерно раз в ${chaosEvery.value} с` : 'Случайные аварии выключены' })
    await refresh()
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Не удалось переключить', text: errorText(e) })
  }
}
const ago = (at: number) => `${Math.max(0, Math.round(Date.now() / 1000 - at))} с назад`

/** Fast-forward for the show: vehicles drive and load N times faster; 1 is normal time. */
async function setSpeed(scale: number) {
  try {
    await sim('/time', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scale }) })
    toasts.push({ kind: 'info', title: scale === 1 ? 'Время идёт как обычно' : `Время ускорено в ${scale} раз` })
    await refresh()
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Не удалось изменить скорость', text: errorText(e) })
  }
}

async function stopAll() {
  await sim('/scenarios/stop', { method: 'POST' })
  toasts.push({ kind: 'info', title: 'Все сценарии остановлены' })
  await refresh()
}

let timer: ReturnType<typeof setInterval>
onMounted(async () => {
  try {
    scenarios.value = await sim<Scenario[]>('/scenarios')
  } catch (e) {
    error.value = `Симулятор недоступен: ${errorText(e)}`
  }
  await refresh()
  timer = setInterval(refresh, 2000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <section class="cheat panel">
    <header class="row">
      <h2 class="grow">Сценарии демо</h2>
      <button class="small danger" @click="stopAll">Стоп всё</button>
      <button class="small ghost" aria-label="Закрыть" @click="emit('close')">✕</button>
    </header>
    <div v-if="error" class="muted">{{ error }}</div>
    <div class="chaos" :class="{ on: status?.chaos.on }">
      <button class="chaos-btn" @click="setChaos(!status?.chaos.on)">
        🎲 Случайные аварии: <b>{{ status?.chaos.on ? 'ВКЛ' : 'выкл' }}</b>
      </button>
      <label class="every">раз в
        <select v-model.number="chaosEvery" @change="status?.chaos.on && setChaos(true)">
          <option v-for="s in CHAOS_EVERY" :key="s" :value="s">~{{ s }} с</option>
        </select>
      </label>
      <div v-for="e in status?.chaos.recent.slice(0, 4) ?? []" :key="e.at" class="chaos-log">
        <span class="grow">{{ e.what }}</span><small class="muted">{{ ago(e.at) }}</small>
      </div>
    </div>
    <div class="speed">
      <span class="grow">Скорость времени</span>
      <button v-for="s in SPEEDS" :key="s" class="small" :class="{ on: (status?.time_scale ?? 1) === s }" @click="setSpeed(s)">
        {{ s === 1 ? '▶ норма' : `⏩ ×${s}` }}
      </button>
    </div>
    <div class="list">
      <button v-for="s in scenarios" :key="s.name" class="scenario" :disabled="!!busy" :title="s.description" @click="run(s.name)">
        <b>{{ s.title }}</b>
        <small class="muted">{{ s.description }}</small>
      </button>
    </div>
    <div v-if="status?.effects.length" class="effects">
      <h3>Активно</h3>
      <div v-for="e in status.effects" :key="e.kind + e.target" class="row">
        <span class="badge sev-warning">{{ EFFECT_LABEL[e.kind] ?? e.kind }}</span>
        <span class="mono grow">{{ e.target }}</span>
        <small class="muted">{{ Math.ceil(e.left_s / 60) }} мин</small>
      </div>
    </div>
    <div v-for="r in status?.runs.filter((r) => r.error) ?? []" :key="r.id" class="muted">⚠ {{ r.scenario }}: {{ r.error }}</div>
  </section>
</template>

<style scoped>
.cheat {
  display: grid;
  gap: 10px;
  padding: 12px;
  max-height: 100%;
  overflow: auto;
}
.chaos {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
}
.chaos.on {
  border-color: var(--st-critical);
  box-shadow: 0 0 12px #ef444455;
}
.chaos-btn {
  justify-content: center;
}
.chaos.on .chaos-btn {
  background: #7f1d1d;
  border-color: var(--st-critical);
  color: #fecaca;
  animation: pulse 1.4s infinite;
}
@keyframes pulse {
  50% {
    opacity: 0.75;
  }
}
.every {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted);
}
.chaos-log {
  grid-column: 1 / -1;
  display: flex;
  gap: 8px;
  font-size: 11px;
}
.speed {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px;
  border-radius: 8px;
  background: var(--bg);
}
.speed .grow {
  font-size: 12px;
  color: var(--text-muted);
}
.speed button.on {
  border-color: var(--accent);
  color: var(--accent);
}
.list {
  display: grid;
  gap: 6px;
}
.scenario {
  display: grid;
  justify-items: start;
  text-align: left;
  white-space: normal;
  gap: 2px;
  padding: 8px 10px;
}
.scenario small {
  font-size: 11px;
}
.effects {
  display: grid;
  gap: 6px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
</style>
