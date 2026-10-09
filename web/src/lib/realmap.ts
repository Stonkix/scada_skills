import maplibregl, { type CustomLayerInterface, type StyleSpecification } from 'maplibre-gl'
import * as THREE from 'three'
import type { Building, Severity, Vehicle, VehicleLive } from '@/api/types'
import type { Georef } from './geo'
import {
  ALERT_COLOR,
  STATUS_COLOR,
  buildingModel,
  selectionRing,
  vehicleModel,
  type BuildingModel,
  type Pickable,
  type VehicleModel,
} from './models3d'

/** Basemaps: raster tiles need internet; without it the site is still drawn on the plain background. */
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

interface Mover {
  model: VehicleModel
  x: number
  y: number
  heading: number
  tx: number
  ty: number
  theading: number
}

const SNAP_M = 60 // farther than this (a reconnect, a teleporting scenario) -> jump, don't glide
const EASE_PER_S = 3.5

/**
 * three.js scene drawn inside MapLibre's WebGL context as a custom 3D layer.
 * The scene lives in plan metres (z up); one matrix places plan (0, 0) at the georef origin,
 * scales metres to Mercator units and turns the plan by `rotation_deg`.
 */
export class SiteLayer implements CustomLayerInterface {
  readonly id = 'site-3d'
  readonly type = 'custom' as const
  readonly renderingMode = '3d' as const

  private map: maplibregl.Map | null = null
  private renderer: THREE.WebGLRenderer | null = null
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.Camera()
  private readonly base: THREE.Matrix4
  private readonly buildingsRoot = new THREE.Group()
  private readonly vehiclesRoot = new THREE.Group()
  private readonly ring = selectionRing()
  private buildings = new Map<string, BuildingModel>()
  private movers = new Map<string, Mover>()
  private alerted = new Map<string, Severity>()
  private selected: Pickable | null = null
  private last = performance.now()
  private readonly raycaster = new THREE.Raycaster()

  constructor(georef: Georef) {
    const origin = maplibregl.MercatorCoordinate.fromLngLat([georef.origin_lon, georef.origin_lat], 0)
    const s = origin.meterInMercatorCoordinateUnits()
    this.base = new THREE.Matrix4()
      .makeTranslation(origin.x, origin.y, origin.z)
      .scale(new THREE.Vector3(s, -s, s)) // Mercator y grows southward
      .multiply(new THREE.Matrix4().makeRotationZ((-(georef.rotation_deg ?? 0) * Math.PI) / 180))

    this.scene.add(new THREE.HemisphereLight('#e0f2fe', '#475569', 2.2))
    const sun = new THREE.DirectionalLight('#fff7ed', 2.4)
    sun.position.set(-0.5, -0.9, 0.8) // from the south-west: the yard-facing walls are lit
    this.scene.add(sun, this.buildingsRoot, this.vehiclesRoot, this.ring)
  }

  onAdd(map: maplibregl.Map, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map = map
    this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl as WebGL2RenderingContext, antialias: true })
    this.renderer.autoClear = false
  }

  onRemove() {
    this.scene.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.geometry.dispose()
        ;(Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose())
      }
    })
    this.renderer?.dispose()
    this.renderer = null
    this.map = null
  }

  setBuildings(list: Building[]) {
    this.buildingsRoot.clear()
    this.buildings = new Map(list.map((b) => [b.id, buildingModel(b)]))
    for (const m of this.buildings.values()) this.buildingsRoot.add(m.group)
    this.repaint()
  }

  /** Live positions; models are created on first sight and glide towards each new fix. */
  updateVehicles(live: Iterable<VehicleLive>, registry: Map<string, Vehicle>) {
    for (const v of live) {
      let m = this.movers.get(v.vehicle_id)
      if (!m) {
        const model = vehicleModel({ id: v.vehicle_id, kind: registry.get(v.vehicle_id)?.kind ?? 'truck' })
        this.vehiclesRoot.add(model.group)
        m = { model, x: v.geo.x, y: v.geo.y, heading: v.heading_deg, tx: v.geo.x, ty: v.geo.y, theading: v.heading_deg }
        this.movers.set(v.vehicle_id, m)
      }
      if (Math.hypot(v.geo.x - m.x, v.geo.y - m.y) > SNAP_M) {
        m.x = v.geo.x
        m.y = v.geo.y
        m.heading = v.heading_deg
      }
      m.tx = v.geo.x
      m.ty = v.geo.y
      // standing vehicles report heading 0: keep the last real one
      if (v.speed_kmh > 0.5) m.theading = v.heading_deg
      m.model.paint.color.set(this.alerted.has(v.vehicle_id) ? ALERT_COLOR : STATUS_COLOR[v.status])
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

  /** Building or vehicle under a canvas pixel. */
  pick(px: number, py: number): Pickable | null {
    const canvas = this.map?.getCanvas()
    if (!canvas) return null
    const ndc = (v: number, size: number) => (v / size) * 2 - 1
    const nx = ndc(px, canvas.clientWidth)
    const ny = -ndc(py, canvas.clientHeight)
    const inv = this.camera.projectionMatrix.clone().invert()
    const near = new THREE.Vector3(nx, ny, -1).applyMatrix4(inv)
    const far = new THREE.Vector3(nx, ny, 1).applyMatrix4(inv)
    this.raycaster.set(near, far.sub(near).normalize())
    // vehicles first: a truck parked at a dock must win over the warehouse behind it
    for (const root of [this.vehiclesRoot, this.buildingsRoot]) {
      for (const hit of this.raycaster.intersectObject(root, true)) {
        for (let o: THREE.Object3D | null = hit.object; o; o = o.parent) {
          if (o.userData.pick) return o.userData.pick as Pickable
        }
      }
    }
    return null
  }

  /** Where to fly to for a selection, plan metres. */
  positionOf(sel: Pickable): [number, number] | null {
    if (sel.kind === 'vehicle') {
      const m = this.movers.get(sel.id)
      return m ? [m.tx, m.ty] : null
    }
    const b = this.buildings.get(sel.id)
    if (!b) return null
    const c = new THREE.Box3().setFromObject(b.group).getCenter(new THREE.Vector3())
    return [c.x, c.y]
  }

  private repaint() {
    this.map?.triggerRepaint()
  }

  render(_gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: ArrayLike<number>) {
    if (!this.renderer || !this.map) return
    const now = performance.now()
    const dt = Math.min(0.25, (now - this.last) / 1000)
    this.last = now
    const t = now / 1000
    let busy = false

    // zoomed out, real-size vehicles are specks: exaggerate them, up to 3x
    const vscale = Math.min(3, Math.max(1, 2 ** (17.3 - this.map.getZoom())))
    const k = 1 - Math.exp(-EASE_PER_S * dt)
    for (const m of this.movers.values()) {
      const dx = m.tx - m.x
      const dy = m.ty - m.y
      const dh = ((m.theading - m.heading + 540) % 360) - 180
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
    }

    // buildings with a live alert pulse red (critical faster)
    for (const [id, b] of this.buildings) {
      const sev = this.alerted.get(id)
      for (const mat of b.walls) {
        if (sev === 'critical' || sev === 'warning') {
          const pulse = 0.5 + 0.5 * Math.sin(t * (sev === 'critical' ? 6 : 3))
          mat.emissive.set(sev === 'critical' ? ALERT_COLOR : '#f59e0b').multiplyScalar(0.25 + 0.45 * pulse)
          busy = true
        } else {
          mat.emissive.setRGB(0, 0, 0)
        }
      }
    }

    const pos = this.selected ? this.positionOf(this.selected) : null
    this.ring.visible = !!pos
    if (pos && this.selected) {
      const b = this.selected.kind === 'building' ? this.buildings.get(this.selected.id) : null
      const size = b ? new THREE.Box3().setFromObject(b.group).getSize(new THREE.Vector3()) : null
      const radius = size ? Math.hypot(size.x, size.y) / 2 + 6 : 9 * vscale
      this.ring.position.set(pos[0], pos[1], 0.3)
      this.ring.scale.setScalar(radius * (1 + 0.06 * Math.sin(t * 4)))
      busy = true
    }

    this.camera.projectionMatrix = new THREE.Matrix4().fromArray(Array.from(matrix)).multiply(this.base)
    this.renderer.resetState()
    this.renderer.render(this.scene, this.camera)
    if (busy) this.map.triggerRepaint()
  }
}

/** Metres -> pixels at a zoom level, for line widths given in metres. */
export function metresToPixels(lat: number, zoom: number, metres: number): number {
  return metres / ((40_075_016.686 * Math.cos((lat * Math.PI) / 180)) / 2 ** (zoom + 9))
}
