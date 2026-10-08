import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'
import { api, unwrap } from '@/api/client'
import type { Alert, SensorLive, VehicleLive, WsMessage } from '@/api/types'
import { LiveSocket, type WsStatus } from '@/lib/ws'
import { alarm } from '@/lib/sound'
import { useToasts } from './toasts'

const SEVERITY_RANK = { info: 0, warning: 1, critical: 2 } as const

/**
 * Live state: snapshot from GET /state/live + /alerts, then kept current by WS /ws/live.
 * Maps are shallow refs triggered manually: hundreds of updates per second must not
 * make Vue deep-track every vehicle.
 */
export const useLive = defineStore('live', () => {
  const sensors = shallowRef(new Map<string, SensorLive>())
  const vehicles = shallowRef(new Map<string, VehicleLive>())
  const people = shallowRef(new Map<string, number>())
  const alerts = shallowRef(new Map<number, Alert>())
  const status = ref<WsStatus>('closed')
  const lastMessage = ref<number>(0)
  const soundOn = ref(true)
  let socket: LiveSocket | null = null
  let pending = false

  /** Coalesce bursts of messages into one reactive update per animation frame. */
  function flush() {
    if (pending) return
    pending = true
    requestAnimationFrame(() => {
      pending = false
      triggerRef(sensors)
      triggerRef(vehicles)
      triggerRef(people)
      triggerRef(alerts)
    })
  }

  function onAlert(a: Alert) {
    const before = alerts.value.get(a.id)
    if (a.status === 'resolved') alerts.value.delete(a.id)
    else alerts.value.set(a.id, a)
    const escalatedToCritical = a.severity === 'critical' && (!before || SEVERITY_RANK[before.severity] < 2)
    if (a.status === 'open' && (!before || escalatedToCritical)) {
      useToasts().push({ kind: a.severity, title: a.title, text: a.message, alertId: a.id })
      if (a.severity === 'critical' && soundOn.value) alarm()
    }
  }

  function onMessage(msg: WsMessage) {
    lastMessage.value = Date.now()
    switch (msg.type) {
      case 'sensor':
        sensors.value.set(msg.data.sensor_id, msg.data)
        break
      case 'vehicle':
        vehicles.value.set(msg.data.vehicle_id, msg.data)
        break
      case 'zone':
        people.value.set(msg.data.zone_id, msg.data.people)
        break
      case 'alert':
        onAlert(msg.data)
        break
    }
    flush()
  }

  async function start() {
    const [state, page] = await Promise.all([
      api.GET('/state/live').then(unwrap),
      api.GET('/alerts', { params: { query: { status: ['open', 'ack'], limit: 500 } } }).then(unwrap),
    ])
    sensors.value = new Map(state.sensors.map((s) => [s.sensor_id, s]))
    vehicles.value = new Map(state.vehicles.map((v) => [v.vehicle_id, v]))
    people.value = new Map(state.zones.map((z) => [z.zone_id, z.people]))
    alerts.value = new Map(page.items.map((a) => [a.id, a]))
    if (!socket) {
      socket = new LiveSocket(onMessage, (s) => (status.value = s))
      socket.start()
    }
  }

  function stop() {
    socket?.stop()
    socket = null
  }

  function replaceAlert(a: Alert) {
    onAlert(a)
    flush()
  }

  const openAlerts = computed(() =>
    [...alerts.value.values()].sort(
      (a, b) =>
        Number(a.status !== 'open') - Number(b.status !== 'open') ||
        SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
        b.opened_at.localeCompare(a.opened_at),
    ),
  )
  const criticalCount = computed(() => openAlerts.value.filter((a) => a.status === 'open' && a.severity === 'critical').length)
  /** Object ids (sensor / vehicle / building) with a live alert, worst severity first. */
  const alertedObjects = computed(() => {
    const out = new Map<string, Alert['severity']>()
    for (const a of openAlerts.value) {
      for (const id of [a.sensor_id, a.vehicle_id, a.building_id]) {
        if (id && !out.has(id)) out.set(id, a.severity)
      }
    }
    return out
  })

  return {
    sensors, vehicles, people, alerts, status, lastMessage, soundOn,
    start, stop, replaceAlert, openAlerts, criticalCount, alertedObjects,
  }
})
