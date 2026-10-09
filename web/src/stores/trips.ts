import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, unwrap } from '@/api/client'
import type { Trip } from '@/api/types'

const REFRESH_MS = 15_000 // waybills change on loading / departure / arrival: minutes apart

/** Trips under way (GET /trips?active=true), refreshed in the background while logged in. */
export const useTrips = defineStore('trips', () => {
  const active = ref<Trip[]>([])
  let timer = 0

  async function load() {
    try {
      active.value = unwrap(await api.GET('/trips', { params: { query: { active: true, limit: 500 } } }))
    } catch {
      // keep the last list: the next refresh will try again
    }
  }

  function start() {
    if (timer) return
    void load()
    timer = window.setInterval(load, REFRESH_MS)
  }

  function stop() {
    window.clearInterval(timer)
    timer = 0
    active.value = []
  }

  const byVehicle = computed(() => new Map(active.value.map((t) => [t.vehicle_id, t])))
  const enRoute = computed(() => active.value.filter((t) => t.status === 'en_route'))

  return { active, byVehicle, enRoute, load, start, stop }
})
