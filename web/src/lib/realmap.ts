import maplibregl, { type CustomLayerInterface, type StyleSpecification } from 'maplibre-gl'
import * as THREE from 'three'
import type { Building, ObjectsResponse, Sensor, Severity, Vehicle, VehicleLive } from '@/api/types'
import { toLngLat, type Georef } from './geo'
import {
  ALERT_COLOR,
  SENSOR_STATUS_COLOR,
  STATUS_COLOR,
  buildingModel,
  floorBase,
  selectionRing,
  sensorPin,
  storey,
  vehicleModel,
  xrayShell,
  type BuildingModel,
  type Pickable,
  type VehicleModel,
} from './models3d'

/** Basemaps: raster tiles need internet; without it the sites are still drawn on the plain background. */
export const BASEMAPS = {
  scheme: 'Схема',
  satellite: 'Спутник',
} as const
export type Basemap = keyof typeof BASEMAPS

export function baseStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        maxzoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      },
      satellite: {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        maxzoom: 19,
        attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#0a0f1c' } },
      // the dark UI around it: dim and desaturate the OSM scheme instead of shipping a custom style
      {
        id: 'scheme',
        type: 'raster',
        source: 'osm',
        paint: { 'raster-brightness-max': 0.62, 'raster-saturation': -0.55, 'raster-contrast': 0.05 },
      },
      { id: 'satellite', type: 'raster', source: 'satellite', layout: { visibility: 'none' } },
    ],
  }
}

export function setBasemap(map: maplibregl.Map, b: Basemap) {
  for (const id of Object.keys(BASEMAPS)) map.setLayoutProperty(id, 'visibility', id === b ? 'visible' : 'none')
}

/**
 * Plan metres around (ax, ay) -> Mercator, linearised at that point. The sites are tens of kilometres
 * apart; one transform for the whole region would drift by tens of metres from the basemap, so every
 * site and every vehicle gets its own anchor.
 */
export function anchorMatrix(g: Georef, ax: number, ay: number): THREE.Matrix4 {
  const D = 100
  const M = (x: number, y: number) => maplibregl.MercatorCoordinate.fromLngLat(toLngLat(g, x, y), 0)
  const a = M(ax, ay)
  const ex = M(ax + D, ay)
  const ey = M(ax, ay + D)
  const jx = [(ex.x - a.x) / D, (ex.y - a.y) / D]
  const jy = [(ey.x - a.x) / D, (ey.y - a.y) / D]
  const s = a.meterInMercatorCoordinateUnits()
  return new THREE.Matrix4().set(
    jx[0], jy[0], 0, a.x - (jx[0] * ax + jy[0] * ay),
    jx[1], jy[1], 0, a.y - (jx[1] * ax + jy[1] * ay),
    0, 0, s, 0,
    0, 0, 0, 1,
  )
}

interface Mover {
  id: string
  model: VehicleModel
  /** Shown position: eases towards the predicted one. */
  x: number
  y: number
  heading: number
  /** Last GNSS fix and when it arrived. */
  fx: number
  fy: number
  fheading: number
  speed: number
  fixAt: number
  status: VehicleLive['status']
  anchor: [number, number]
  matrix: THREE.Matrix4
}

const SNAP_M = 400 // farther than this from the shown position (a reconnect) -> jump, don't glide
const EASE_PER_S = 2.5
const MAX_PREDICT_S = 45 // trackers report at least every 30 s while moving; don't run off for longer
const REANCHOR_M = 250
const VEHICLES_3D_MIN_ZOOM = 13.5
const FLASH_MS = 900

export interface InspectState {
  siteId: string
  /** X-rayed building; without it only the site's outdoor sensors are pinned. */
  building?: Building
  floor: number
  /** Sensors to pin: the building's on this floor plus the site's outdoor ones. */
  sensors: Sensor[]
  rooms: ObjectsResponse['rooms']
  sensorStatus: (id: string) => string
  roomStatus: (id: string) => string
}

/**
 * three.js scene drawn inside MapLibre's WebGL context as a custom 3D layer.
 * Geometry lives in plan metres (z up); each anchored root is drawn with its own matrix.
 */
export class SiteLayer implements CustomLayerInterface {
  readonly id = 'site-3d'
  readonly type = 'custom' as const
  readonly renderingMode = '3d' as const

  private map: maplibregl.Map | null = null
  private renderer: THREE.WebGLRenderer | null = null
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.Camera()
  private readonly raycaster = new THREE.Raycaster()
  private readonly ring = selectionRing()
  private readonly ringRoot = new THREE.Group()
  private sites: { id: string; group: THREE.Group; matrix: THREE.Matrix4 }[] = []
  private buildings = new Map<string, { model: BuildingModel; siteIdx: number }>()
  private movers = new Map<string, Mover>()
  private alerted = new Map<string, Severity>()
  private selected: Pickable | null = null
  private inspect: { id: string | null; group: THREE.Group; pins: THREE.Group[]; siteIdx: number } | null = null
  private flashes = new Map<string, number>() // sensor id -> flash start (ms)
  private last = performance.now()
  private drawn: { root: THREE.Object3D; matrix: THREE.Matrix4 }[] = []
  private projection: THREE.Matrix4 | null = null
  /** Called once per frame after the scene moved: the view syncs 2D overlays and the camera follow. */
  onFrame: (() => void) | null = null

  constructor(private readonly georef: Georef) {
    this.scene.add(new THREE.HemisphereLight('#e0f2fe', '#475569', 2.2))
    const sun = new THREE.DirectionalLight('#fff7ed', 2.4)
    sun.position.set(-0.5, -0.9, 0.8) // from the south-west: the yard-facing walls are lit
    this.scene.add(sun)
    this.ringRoot.add(this.ring)
    this.ringRoot.visible = false
    this.scene.add(this.ringRoot)
  }

  onAdd(map: maplibregl.Map, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map = map
    this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl as WebGL2RenderingContext, antialias: true })
    this.renderer.autoClear = false
  }

  onRemove() {
    this.scene.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.geometry.dispose()
        ;(Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose())
      }
    })
    this.renderer?.dispose()
    this.renderer = null
    this.map = null
  }

  /** Static plan: one anchored group of buildings per site. */
  setPlan(o: ObjectsResponse) {
    this.setInspect(null)
    for (const s of this.sites) this.scene.remove(s.group)
    this.sites = o.sites.map((site) => {
      const ring = site.geometry.coordinates[0] as [number, number][]
      const xs = ring.map((p) => p[0])
      const ys = ring.map((p) => p[1])
      const group = new THREE.Group()
      group.visible = false
      this.scene.add(group)
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2
      const cy = (Math.min(...ys) + Math.max(...ys)) / 2
      return { id: site.id, group, matrix: anchorMatrix(this.georef, cx, cy) }
    })
    this.buildings.clear()
    for (const b of o.buildings) {
      const siteIdx = this.sites.findIndex((s) => s.id === b.site_id)
      if (siteIdx < 0) continue // drawn outside every site: nothing to anchor it to
      const model = buildingModel(b)
      this.sites[siteIdx].group.add(model.group)
      this.buildings.set(b.id, { model, siteIdx })
    }
    this.repaint()
  }

  /** New GNSS fixes; models are created on first sight. */
  updateVehicles(live: Iterable<VehicleLive>, registry: Map<string, Vehicle>) {
    const now = performance.now()
    for (const v of live) {
      let m = this.movers.get(v.vehicle_id)
      if (!m) {
        const model = vehicleModel({ id: v.vehicle_id, kind: registry.get(v.vehicle_id)?.kind ?? 'truck' })
        model.group.visible = false
        this.scene.add(model.group)
        m = {
          id: v.vehicle_id, model, x: v.geo.x, y: v.geo.y, heading: v.heading_deg, fx: v.geo.x, fy: v.geo.y,
          fheading: v.heading_deg, speed: v.speed_kmh, fixAt: now, status: v.status, anchor: [v.geo.x, v.geo.y],
          matrix: anchorMatrix(this.georef, v.geo.x, v.geo.y),
        }
        this.movers.set(v.vehicle_id, m)
        continue
      }
      m.status = v.status
      if (v.geo.x === m.fx && v.geo.y === m.fy && v.speed_kmh === m.speed) continue // the same fix again
      if (Math.hypot(v.geo.x - m.x, v.geo.y - m.y) > SNAP_M) {
        m.x = v.geo.x
        m.y = v.geo.y
        m.heading = v.heading_deg
      }
      m.fx = v.geo.x
      m.fy = v.geo.y
      m.speed = v.speed_kmh
      m.fixAt = now
      // standing vehicles report heading 0: keep the last real one
      if (v.speed_kmh > 0.5) m.fheading = v.heading_deg
    }
    this.repaint()
  }

  setAlerted(ids: Map<string, Severity>) {
    this.alerted = ids
    this.repaint()
  }

  setSelected(sel: Pickable | null) {
    this.selected = sel
    this.repaint()
  }

  get inspected(): string | null {
    return this.inspect?.id ?? null
  }

  /** X-ray one building: glass shell, the chosen storey with rooms, sensor pins. null restores the model. */
  setInspect(state: InspectState | null) {
    if (this.inspect) {
      this.sites[this.inspect.siteIdx]?.group.remove(this.inspect.group)
      const prev = this.inspect.id ? this.buildings.get(this.inspect.id) : undefined
      if (prev) prev.model.group.visible = true
      this.inspect = null
    }
    const siteIdx = state ? this.sites.findIndex((s) => s.id === state.siteId) : -1
    if (state && siteIdx >= 0) {
      const group = new THREE.Group()
      const b = state.building
      const entry = b && this.buildings.get(b.id)
      let z = 0.45
      if (b && entry) {
        entry.model.group.visible = false
        group.add(xrayShell(b))
        const rooms = state.rooms.filter((r) => r.building_id === b.id && r.floor === state.floor)
        group.add(storey(b, state.floor, rooms, (id) => SENSOR_STATUS_COLOR[state.roomStatus(id)] ?? '#475569'))
        z += floorBase(b, state.floor)
      }
      const pins = state.sensors
        .filter((s) => s.geo)
        .map((s) => sensorPin(s, s.building_id ? z : 0, SENSOR_STATUS_COLOR[state.sensorStatus(s.id)] ?? '#64748b'))
      if (pins.length) group.add(...pins)
      this.sites[siteIdx].group.add(group)
      this.inspect = { id: entry ? b!.id : null, group, pins, siteIdx }
    }
    this.repaint()
  }

  /** A sensor reported: its pin (if shown) flashes white and swells for a moment. */
  flash(sensorId: string) {
    if (this.inspect?.pins.some((p) => (p.userData.pick as Pickable).id === sensorId)) {
      this.flashes.set(sensorId, performance.now())
      this.repaint()
    }
  }

  /** Recolour the pins of the inspected building without rebuilding it. */
  recolorPins(status: (id: string) => string) {
    for (const pin of this.inspect?.pins ?? []) {
      const c = SENSOR_STATUS_COLOR[status((pin.userData.pick as Pickable).id)] ?? '#64748b'
      const mat = pin.userData.head as THREE.MeshLambertMaterial
      mat.color.set(c)
      mat.emissive.set(c)
    }
    this.repaint()
  }

  /** Building, vehicle or sensor pin under a canvas pixel: the nearest to the camera. */
  pick(px: number, py: number): Pickable | null {
    const canvas = this.map?.getCanvas()
    const P = this.projection
    if (!canvas || !P) return null
    const nx = (px / canvas.clientWidth) * 2 - 1
    const ny = -((py / canvas.clientHeight) * 2 - 1)
    let best: { pick: Pickable; depth: number } | null = null
    // each anchored root has its own transform: cast the ray in its local frame, compare depths in clip space
    for (const { root, matrix } of this.drawn) {
      if (root === this.ringRoot) continue
      const full = P.clone().multiply(matrix)
      const inv = full.clone().invert()
      const near = new THREE.Vector3(nx, ny, -1).applyMatrix4(inv)
      const far = new THREE.Vector3(nx, ny, 1).applyMatrix4(inv)
      this.raycaster.set(near, far.sub(near).normalize())
      root.visible = true
      const hits = this.raycaster.intersectObject(root, true)
      root.visible = false
      for (const hit of hits) {
        let pick: Pickable | null = null
        for (let o: THREE.Object3D | null = hit.object; o && !pick; o = o.parent) pick = (o.userData.pick as Pickable) ?? null
        if (!pick) continue
        const depth = hit.point.clone().applyMatrix4(full).z - (pick.kind === 'building' ? 0 : 1e-4) // small things win ties
        if (!best || depth < best.depth) best = { pick, depth }
        break
      }
    }
    return best?.pick ?? null
  }

  /** Current shown position of a vehicle (plan metres), for the camera to follow. */
  vehiclePosition(id: string): [number, number] | null {
    const m = this.movers.get(id)
    return m ? [m.x, m.y] : null
  }

  /** Shown positions of all vehicles: the 2D icon layer for zoomed-out views. */
  vehicles(): { id: string; x: number; y: number; heading: number; status: VehicleLive['status'] }[] {
    return [...this.movers.values()].map((m) => ({ id: m.id, x: m.x, y: m.y, heading: m.heading, status: m.status }))
  }

  private repaint() {
    this.map?.triggerRepaint()
  }

  private draw(root: THREE.Object3D, matrix: THREE.Matrix4, P: THREE.Matrix4) {
    root.visible = true
    this.camera.projectionMatrix.multiplyMatrices(P, matrix)
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert()
    this.renderer!.render(this.scene, this.camera)
    root.visible = false
    this.drawn.push({ root, matrix })
  }

  render(_gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: ArrayLike<number>) {
    if (!this.renderer || !this.map) return
    const now = performance.now()
    const dt = Math.min(0.25, (now - this.last) / 1000)
    this.last = now
    const t = now / 1000
    let busy = false
    const zoom = this.map.getZoom()

    // vehicles: dead reckoning from the last fix along its heading; the shown position eases towards it
    const vscale = Math.min(3, Math.max(1, 2 ** (17.3 - zoom)))
    const k = 1 - Math.exp(-EASE_PER_S * dt)
    for (const m of this.movers.values()) {
      const ahead = m.speed > 0.5 ? Math.min((now - m.fixAt) / 1000, MAX_PREDICT_S) * (m.speed / 3.6) : 0
      const h = (m.fheading * Math.PI) / 180
      const dx = m.fx + Math.sin(h) * ahead - m.x
      const dy = m.fy + Math.cos(h) * ahead - m.y
      const dh = ((m.fheading - m.heading + 540) % 360) - 180
      if (Math.abs(dx) + Math.abs(dy) > 0.02 || Math.abs(dh) > 0.2) {
        m.x += dx * k
        m.y += dy * k
        m.heading += dh * k
        busy = true
      }
      const g = m.model.group
      g.position.set(m.x, m.y, 0)
      g.rotation.z = (-m.heading * Math.PI) / 180 // compass heading is clockwise from plan north (+y)
      g.scale.setScalar(vscale)
      m.model.paint.color.set(this.alerted.has(m.id) ? ALERT_COLOR : STATUS_COLOR[m.status])
      if (Math.hypot(m.x - m.anchor[0], m.y - m.anchor[1]) > REANCHOR_M) {
        m.anchor = [m.x, m.y]
        m.matrix = anchorMatrix(this.georef, m.x, m.y)
      }
    }

    // buildings with a live alert pulse red (critical faster)
    for (const [id, b] of this.buildings) {
      const sev = this.alerted.get(id)
      for (const mat of b.model.walls) {
        if (sev === 'critical' || sev === 'warning') {
          const pulse = 0.5 + 0.5 * Math.sin(t * (sev === 'critical' ? 6 : 3))
          mat.emissive.set(sev === 'critical' ? ALERT_COLOR : '#f59e0b').multiplyScalar(0.25 + 0.45 * pulse)
          busy = true
        } else {
          mat.emissive.setRGB(0, 0, 0)
        }
      }
    }
    // pins are a metre across: grow them when zoomed out so they stay clickable
    const pscale = Math.min(4, Math.max(1, 2 ** (18.6 - zoom)))
    for (const pin of this.inspect?.pins ?? []) {
      pin.scale.setScalar(pscale)
      const id = (pin.userData.pick as Pickable).id
      const head = pin.children[1]
      const mat = pin.userData.head as THREE.MeshLambertMaterial
      const since = this.flashes.has(id) ? (now - this.flashes.get(id)!) / FLASH_MS : 1
      if (since < 1) {
        const f = 1 - since // 1 -> 0
        head.scale.setScalar(1 + 0.9 * f)
        mat.emissiveIntensity = 0.45 + 2.5 * f
      } else {
        this.flashes.delete(id)
        head.scale.setScalar(1 + 0.12 * Math.sin(t * 3 + pin.position.x))
        mat.emissiveIntensity = 0.45
      }
      busy = true
    }

    // selection ring under the selected vehicle or building, drawn in its anchor's frame
    let ringMatrix: THREE.Matrix4 | null = null
    const sel = this.selected
    const mover = sel?.kind === 'vehicle' ? this.movers.get(sel.id) : undefined
    const bld = sel?.kind === 'building' ? this.buildings.get(sel.id) : undefined
    if (mover) {
      this.ring.position.set(mover.x, mover.y, 0.3)
      this.ring.scale.setScalar(9 * vscale * (1 + 0.06 * Math.sin(t * 4)))
      ringMatrix = mover.matrix
    } else if (bld) {
      const visible = this.inspect && this.inspect.id === sel!.id ? this.inspect.group : bld.model.group
      const box = new THREE.Box3().setFromObject(visible)
      const c = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3())
      this.ring.position.set(c.x, c.y, 0.3)
      this.ring.scale.setScalar((Math.hypot(size.x, size.y) / 2 + 6) * (1 + 0.03 * Math.sin(t * 4)))
      ringMatrix = this.sites[bld.siteIdx].matrix
    }

    const P = new THREE.Matrix4().fromArray(Array.from(matrix))
    this.projection = P
    this.drawn = []
    this.renderer.resetState()
    for (const s of this.sites) this.draw(s.group, s.matrix, P)
    if (zoom >= VEHICLES_3D_MIN_ZOOM) for (const m of this.movers.values()) this.draw(m.model.group, m.matrix, P)
    if (ringMatrix) {
      busy = true
      this.draw(this.ringRoot, ringMatrix, P)
    }
    this.onFrame?.()
    if (busy || [...this.movers.values()].some((m) => m.speed > 0.5)) this.map.triggerRepaint()
  }
}

/** Metres -> pixels at a zoom level, for line widths given in metres. */
export function metresToPixels(lat: number, zoom: number, metres: number): number {
  return metres / ((40_075_016.686 * Math.cos((lat * Math.PI) / 180)) / 2 ** (zoom + 9))
}

/** Arrow icon for the 2D vehicle layer (SDF: recoloured per status by the layer). */
export function arrowIcon(size = 48): ImageData {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  g.fillStyle = '#fff'
  g.beginPath()
  g.moveTo(size / 2, 3)
  g.lineTo(size - 7, size - 5)
  g.lineTo(size / 2, size - 15)
  g.lineTo(7, size - 5)
  g.closePath()
  g.fill()
  return g.getImageData(0, 0, size, size)
}
