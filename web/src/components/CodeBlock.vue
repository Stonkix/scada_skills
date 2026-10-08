<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ code: string; label?: string }>()
const copied = ref(false)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.code)
  } catch {
    // clipboard API needs a secure context; fall back to a hidden textarea
    const t = document.createElement('textarea')
    t.value = props.code
    document.body.appendChild(t)
    t.select()
    document.execCommand('copy')
    t.remove()
  }
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<template>
  <div class="code">
    <div class="bar">
      <small class="muted">{{ label }}</small>
      <button class="small ghost" @click="copy">{{ copied ? '✓ Скопировано' : 'Копировать' }}</button>
    </div>
    <pre><code>{{ code }}</code></pre>
  </div>
</template>

<style scoped>
.code {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #070b14;
  overflow: hidden;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 6px 3px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
}
pre {
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  max-height: 320px;
  font: 12px/1.5 var(--mono);
  color: #cbd5e1;
  white-space: pre;
}
</style>
