import { onBeforeUnmount, ref } from 'vue'

/** A ticking "now" for relative times ("5 с назад"); stops with the component. */
export function useNow(intervalMs = 5000) {
  const now = ref(Date.now())
  const timer = setInterval(() => (now.value = Date.now()), intervalMs)
  onBeforeUnmount(() => clearInterval(timer))
  return now
}
