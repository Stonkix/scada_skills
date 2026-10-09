<script setup lang="ts">
import { computed } from 'vue'
import { fmtHM } from '@/lib/format'
import type { Selection } from '@/lib/plan'
import { useObjects } from '@/stores/objects'
import { useTrips } from '@/stores/trips'

/** Trucks on the road between sites, nearest arrival first. */
const emit = defineEmits<{ select: [sel: Selection] }>()
const objects = useObjects()
const trips = useTrips()
const siteName = (id: string) => objects.data?.sites.find((s) => s.id === id)?.name ?? id
const list = computed(() => [...trips.enRoute].sort((a, b) => (a.eta ?? '').localeCompare(b.eta ?? '')))
</script>

<template>
  <div v-if="!list.length" class="empty-state">Сейчас в пути никого нет</div>
  <button v-for="t in list" :key="t.id" class="trip" @click="emit('select', { kind: 'vehicle', id: t.vehicle_id })">
    <div class="row">
      <b class="mono grow">{{ t.plate }}</b>
      <small class="muted">прибытие ≈ <b class="mono">{{ fmtHM(t.eta) }}</b></small>
    </div>
    <div class="leg">{{ siteName(t.origin_site_id) }} → {{ siteName(t.destination_site_id) }}</div>
    <small class="muted">{{ t.cargo }} · {{ t.weight_t }} т{{ t.temperature_mode ? ` · ${t.temperature_mode}` : '' }}</small>
  </button>
</template>

<style scoped>
.trip {
  display: grid;
  gap: 2px;
  text-align: left;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
}
.trip:hover {
  border-color: var(--accent);
}
.leg {
  font-size: 12px;
}
</style>
