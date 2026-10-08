import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Toast {
  id: number
  kind: 'info' | 'warning' | 'critical' | 'success' | 'error'
  title: string
  text?: string
  alertId?: number
}

let seq = 0

export const useToasts = defineStore('toasts', () => {
  const items = ref<Toast[]>([])

  function push(t: Omit<Toast, 'id'>, ttlMs = t.kind === 'critical' ? 12000 : 6000) {
    const toast = { ...t, id: ++seq }
    items.value = [toast, ...items.value].slice(0, 5)
    setTimeout(() => dismiss(toast.id), ttlMs)
  }

  function dismiss(id: number) {
    items.value = items.value.filter((t) => t.id !== id)
  }

  return { items, push, dismiss }
})
