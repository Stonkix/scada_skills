<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useToasts } from '@/stores/toasts'

const toasts = useToasts()
const router = useRouter()

function open(alertId?: number) {
  if (alertId) router.push({ name: 'map', query: { alert: String(alertId) } })
}
</script>

<template>
  <div class="toasts" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="t in toasts.items" :key="t.id" class="toast" :class="t.kind" @click="open(t.alertId)">
        <div class="row">
          <b class="grow">{{ t.title }}</b>
          <button class="ghost small" aria-label="Закрыть" @click.stop="toasts.dismiss(t.id)">✕</button>
        </div>
        <div v-if="t.text" class="muted text">{{ t.text }}</div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 16px;
  bottom: 16px;
  display: grid;
  gap: 8px;
  width: min(380px, calc(100vw - 32px));
  z-index: 2000;
}
.toast {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  box-shadow: 0 8px 24px #0008;
  cursor: pointer;
}
.toast.critical,
.toast.error { border-left-color: var(--st-critical); }
.toast.warning { border-left-color: var(--st-warning); }
.toast.success { border-left-color: var(--st-ok); }
.text {
  font-size: 12px;
  margin-top: 2px;
}
.toast-enter-active,
.toast-leave-active { transition: all 0.25s; }
.toast-enter-from,
.toast-leave-to { opacity: 0; transform: translateX(30px); }
</style>
