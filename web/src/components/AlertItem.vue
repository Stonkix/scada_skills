<script setup lang="ts">
import { ref } from 'vue'
import { api, errorText, unwrap } from '@/api/client'
import type { Alert } from '@/api/types'
import { ALERT_KIND_LABEL, SEVERITY_LABEL, STATUS_LABEL, fmtAgo, fmtDateTime } from '@/lib/format'
import { useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ alert: Alert; compact?: boolean }>()
const emit = defineEmits<{ locate: [alert: Alert]; changed: [alert: Alert] }>()

const auth = useAuth()
const live = useLive()
const toasts = useToasts()
const comment = ref('')
const commenting = ref<'ack' | 'resolve' | null>(null)
const busy = ref(false)

async function act(action: 'ack' | 'resolve') {
  busy.value = true
  try {
    const path = action === 'ack' ? '/alerts/{alert_id}/ack' : '/alerts/{alert_id}/resolve'
    const updated = unwrap(
      await api.POST(path, { params: { path: { alert_id: props.alert.id } }, body: { comment: comment.value || null } }),
    )
    live.replaceAlert(updated)
    emit('changed', updated)
    commenting.value = null
    comment.value = ''
  } catch (e) {
    toasts.push({ kind: 'error', title: 'Не удалось', text: errorText(e) })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <article class="alert" :class="[`sev-${alert.severity}`, alert.status]">
    <header class="row">
      <span class="badge" :class="`sev-${alert.severity}`">{{ SEVERITY_LABEL[alert.severity] }}</span>
      <span v-if="alert.status !== 'open'" class="badge muted">{{ STATUS_LABEL[alert.status] }}</span>
      <span v-if="alert.escalation_level" class="badge sev-critical" :title="'Уровень эскалации'">↑{{ alert.escalation_level }}</span>
      <span class="spacer" />
      <small class="muted" :title="fmtDateTime(alert.opened_at)">{{ fmtAgo(alert.opened_at) }}</small>
    </header>
    <div class="title" @click="emit('locate', alert)">{{ alert.title }}</div>
    <div class="msg muted">{{ alert.message }}</div>
    <div v-if="!compact && alert.comment" class="comment">{{ alert.comment }}</div>
    <footer v-if="alert.status !== 'resolved'" class="row wrap">
      <small class="muted">{{ ALERT_KIND_LABEL[alert.kind] }}</small>
      <span class="spacer" />
      <button class="small ghost" @click="emit('locate', alert)">На карте</button>
      <template v-if="auth.can('alerts:ack')">
        <button v-if="alert.status === 'open'" class="small" :disabled="busy" @click="commenting = 'ack'">Подтвердить</button>
        <button class="small" :disabled="busy" @click="commenting = 'resolve'">Закрыть</button>
      </template>
    </footer>
    <form v-if="commenting" class="row" @submit.prevent="act(commenting)">
      <input v-model="comment" class="grow" :placeholder="commenting === 'ack' ? 'Что делаем? (необязательно)' : 'Причина / что сделано'" autofocus />
      <button class="small primary" type="submit" :disabled="busy">OK</button>
      <button class="small ghost" type="button" @click="commenting = null">✕</button>
    </form>
  </article>
</template>

<style scoped>
.alert {
  display: grid;
  gap: 4px;
  padding: 9px 10px;
  border-radius: 6px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-left: 3px solid currentColor;
}
.alert.ack,
.alert.resolved {
  opacity: 0.72;
}
.title {
  color: var(--text);
  font-weight: 600;
  cursor: pointer;
}
.title:hover {
  text-decoration: underline;
}
.msg {
  font-size: 12px;
}
.comment {
  font-size: 12px;
  color: var(--text);
  white-space: pre-line;
  border-left: 2px solid var(--border);
  padding-left: 8px;
}
footer {
  margin-top: 2px;
}
</style>
