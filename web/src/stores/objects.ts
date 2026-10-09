import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, unwrap } from '@/api/client'
import type { ObjectsResponse, Sensor, SensorTypeInfo } from '@/api/types'

/** Static plan + registry from GET /objects, reloaded when the plan version changes. */
export const useObjects = defineStore('objects', () => {
  const data = ref<ObjectsResponse | null>(null)
  const loading = ref(false)

  async function load(force = false) {
    if (data.value && !force) return
    loading.value = true
    try {
      data.value = unwrap(await api.GET('/objects'))
    } finally {
      loading.value = false
    }
  }

  const sensors = computed(() => new Map((data.value?.sensors ?? []).map((s) => [s.id, s])))
  const vehicles = computed(() => new Map((data.value?.vehicles ?? []).map((v) => [v.id, v])))
  const buildings = computed(() => new Map((data.value?.buildings ?? []).map((b) => [b.id, b])))
  const types = computed(() => new Map((data.value?.sensor_types ?? []).map((t) => [t.id, t])))
  const zoneNames = computed(() => {
    const names = new Map<string, string>()
    data.value?.buildings.forEach((b) => names.set(b.id, b.name))
    data.value?.rooms.forEach((r) => names.set(r.id, r.name))
    data.value?.geozones.forEach((z) => names.set(z.id, z.name))
    return names
  })
  const sites = computed(() => new Map((data.value?.sites ?? []).map((s) => [s.id, s])))
  const siteBounds = computed(() =>
    [...sites.value.values()].map((s) => {
      const ring = s.geometry.coordinates[0] as [number, number][]
      const xs = ring.map((p) => p[0])
      const ys = ring.map((p) => p[1])
      return { id: s.id, x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) }
    }),
  )
  /** The site a plan point lies on, or null (on the road between sites). */
  function siteAt(x: number, y: number): string | null {
    return siteBounds.value.find((b) => x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1)?.id ?? null
  }
  /** The site of a sensor: by its building, else by where it stands. */
  function siteOfSensor(id: string): string | null {
    const s = sensors.value.get(id)
    if (!s) return null
    if (s.building_id) return buildings.value.get(s.building_id)?.site_id ?? null
    return s.geo ? siteAt(s.geo.x, s.geo.y) : null
  }
  const maxFloor = computed(() => Math.max(1, ...(data.value?.buildings ?? []).map((b) => b.floors)))

  function metric(sensorType: string, key: string) {
    return (types.value.get(sensorType as SensorTypeInfo['id'])?.metrics ?? []).find((m) => m.key === key)
  }

  function upsertSensor(s: Sensor) {
    if (!data.value) return
    const i = data.value.sensors.findIndex((x) => x.id === s.id)
    if (i >= 0) data.value.sensors[i] = s
    else data.value.sensors.push(s)
  }

  return { data, loading, load, sensors, vehicles, buildings, sites, types, zoneNames, maxFloor, metric, upsertSensor, siteAt, siteOfSensor }
})
