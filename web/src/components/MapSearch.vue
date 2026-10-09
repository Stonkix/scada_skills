<script setup lang="ts">
import { computed, ref } from 'vue'
import { KIND_LABEL, buildIndex, search, type SearchItem } from '@/lib/search'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

/** The search bar over the map: sites, buildings, rooms, sensors, vehicles (plate, cargo, waybill, driver). */
const emit = defineEmits<{ pick: [item: SearchItem] }>()
const objects = useObjects()
const trips = useTrips()

const q = ref('')
const open = ref(false)
const active = ref(0)
const index = computed(() => (objects.data ? buildIndex(objects.data, trips.active) : []))
const results = computed(() => search(index.value, q.value, 10))

function pick(item: SearchItem | undefined) {
  if (!item) return
  emit('pick', item)
  q.value = ''
  open.value = false
  ;(document.activeElement as HTMLElement | null)?.blur()
}

function key(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') active.value = Math.min(active.value + 1, results.value.length - 1)
  else if (e.key === 'ArrowUp') active.value = Math.max(active.value - 1, 0)
  else if (e.key === 'Enter') pick(results.value[active.value])
  else if (e.key === 'Escape') {
    q.value = ''
    open.value = false
    return
  } else return
  e.preventDefault()
}
</script>

<template>
  <div class="search" @focusout="open = false">
    <input
      v-model="q"
      type="search"
      placeholder="Поиск: госномер, груз, путевой лист, здание, датчик…"
      aria-label="Поиск по объектам и технике"
      @focus="open = true"
      @input="(open = true), (active = 0)"
      @keydown="key"
    />
    <ul v-if="open && q.trim()" class="results" role="listbox">
      <li v-if="!results.length" class="muted none">Ничего не найдено</li>
      <li
        v-for="(r, i) in results"
        :key="`${r.kind}:${r.id}`"
        role="option"
        :aria-selected="i === active"
        :class="{ on: i === active }"
        @mousedown.prevent="pick(r)"
        @mousemove="active = i"
      >
        <span class="kind">{{ KIND_LABEL[r.kind] }}</span>
        <span class="title">{{ r.title }}</span>
        <small class="muted">{{ r.subtitle }}</small>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.search {
  position: relative;
  width: min(460px, 100%);
}
input {
  width: 100%;
  padding: 9px 14px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: #111a2cee;
  color: var(--text);
  font: inherit;
  box-shadow: 0 6px 20px #0008;
}
input:focus {
  outline: none;
  border-color: var(--accent);
}
.results {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 10px 30px #000a;
  max-height: 60vh;
  overflow: auto;
}
li {
  display: grid;
  grid-template-columns: 82px 1fr;
  gap: 0 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}
li.on {
  background: var(--panel-2);
}
li .kind {
  grid-row: span 2;
  font-size: 11px;
  color: var(--accent);
  align-self: center;
}
li small {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
li.none {
  display: block;
  padding: 10px;
}
</style>
