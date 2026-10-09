import * as THREE from 'three'
import type { Building, Room, Sensor, Vehicle, VehicleLive } from '@/api/types'

/**
 * Procedural low-poly models in a z-up world of plan metres (x east, y north on the plan).
 * Built from primitives on purpose: no binary assets to ship, license or load offline.
 */

export type Pickable = { kind: 'building' | 'vehicle' | 'sensor'; id: string }

export const STATUS_COLOR: Record<VehicleLive['status'], string> = {
  moving: '#fbbf24',
  idle: '#38bdf8',
  stopped: '#94a3b8',
  offline: '#475569',
}
export const ALERT_COLOR = '#ef4444'

const lambert = (color: string) => new THREE.MeshLambertMaterial({ color })
const SHARED = {
  dark: lambert('#1f2937'),
  glass: lambert('#1e3a5f'),
  tyre: lambert('#111827'),
  white: lambert('#e5e7eb'),
  steel: lambert('#64748b'),
  roof: lambert('#8b97a8'),
  concrete: lambert('#9ca3af'),
  skylight: lambert('#7dd3fc'),
  brick: lambert('#9a3412'),
}

/** A box given by its size and centre, z-up. */
function box(w: number, d: number, h: number, mat: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, d, h), mat)
  m.position.set(x, y, z)
  return m
}

/** Wheel: a cylinder lying along x. */
function wheel(r: number, width: number, x: number, y: number): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, width, 14), SHARED.tyre)
  m.rotation.z = Math.PI / 2
  m.position.set(x, y, r)
  return m
}

/** Triangular prism: profile (u, v) extruded `length` along w; basis maps u, v, w to world axes. */
function prism(profile: [number, number][], length: number, basis: THREE.Matrix4, mat: THREE.Material): THREE.Mesh {
  const geo = new THREE.ExtrudeGeometry(new THREE.Shape(profile.map(([u, v]) => new THREE.Vector2(u, v))), {
    depth: length,
    bevelEnabled: false,
  })
  geo.applyMatrix4(basis)
  return new THREE.Mesh(geo, mat)
}
const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z)
// u -> y, v -> z, w -> x  /  u -> -x, v -> z, w -> y  /  u -> x, v -> z, w -> -y (all right-handed)
const ALONG_X = new THREE.Matrix4().makeBasis(V(0, 1, 0), V(0, 0, 1), V(1, 0, 0))
const ALONG_Y = new THREE.Matrix4().makeBasis(V(-1, 0, 0), V(0, 0, 1), V(0, 1, 0))
const ALONG_NEG_Y = new THREE.Matrix4().makeBasis(V(1, 0, 0), V(0, 0, 1), V(0, -1, 0))

function extrude(ring: [number, number][], height: number, mat: THREE.Material, z0 = 0): THREE.Mesh {
  const pts = ring.slice(0, -1).map(([x, y]) => new THREE.Vector2(x, y))
  const geo = new THREE.ExtrudeGeometry(new THREE.Shape(pts), { depth: height, bevelEnabled: false })
  geo.translate(0, 0, z0)
  return new THREE.Mesh(geo, mat)
}

function bbox(ring: [number, number][]) {
  const xs = ring.map((p) => p[0])
  const ys = ring.map((p) => p[1])
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  return { x0, x1, y0, y1, w: x1 - x0, d: y1 - y0, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 }
}

/** Evenly spaced positions along [a, b], at most every `step` metres, inset by `margin`. */
function spread(a: number, b: number, step: number, margin: number): number[] {
  const n = Math.max(1, Math.floor((b - a - 2 * margin) / step))
  return Array.from({ length: n }, (_, i) => a + margin + ((b - a - 2 * margin) * (i + 0.5)) / n)
}

export interface BuildingModel {
  group: THREE.Group
  /** Wall materials: tinted when the building has a live alert. */
  walls: THREE.MeshLambertMaterial[]
  height: number
}

/** Склад: corrugated box with a gable roof and dock doors on the south (yard) side. */
function warehouse(ring: [number, number][], walls: THREE.MeshLambertMaterial): [THREE.Object3D[], number] {
  const b = bbox(ring)
  const h = 10
  const parts: THREE.Object3D[] = [extrude(ring, h, walls)]
  const rise = Math.min(4, Math.min(b.w, b.d) * 0.12)
  if (b.w >= b.d) {
    const r = prism([[-b.d / 2, 0], [b.d / 2, 0], [0, rise]], b.w, ALONG_X, SHARED.roof)
    r.position.set(b.x0, b.cy, h)
    parts.push(r)
  } else {
    const r = prism([[-b.w / 2, 0], [b.w / 2, 0], [0, rise]], b.d, ALONG_Y, SHARED.roof)
    r.position.set(b.cx, b.y0, h)
    parts.push(r)
  }
  for (const x of spread(b.x0, b.x1, 14, 6)) {
    parts.push(box(4, 0.3, 4.5, SHARED.dark, x, b.y0 - 0.1, 2.25))
    parts.push(box(5, 2.5, 0.25, SHARED.steel, x, b.y0 - 1.25, 5.2)) // canopy
  }
  return [parts, h + rise]
}

/** Цех: brick box, sawtooth roof with glazed skylights, a chimney. */
function production(ring: [number, number][], walls: THREE.MeshLambertMaterial): [THREE.Object3D[], number] {
  const b = bbox(ring)
  const h = 9
  const parts: THREE.Object3D[] = [extrude(ring, h, walls)]
  const pitch = 10
  const n = Math.max(1, Math.floor(b.w / pitch))
  const p = b.w / n
  for (let i = 0; i < n; i++) {
    const tooth = prism([[0, 0], [p, 0], [p, 3]], b.d, ALONG_NEG_Y, SHARED.roof)
    tooth.position.set(b.x0 + i * p, b.y1, h)
    parts.push(tooth)
    parts.push(box(0.2, b.d - 0.4, 2.6, SHARED.skylight, b.x0 + (i + 1) * p - 0.15, b.cy, h + 1.5))
  }
  const chimney = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.6, 26, 12), SHARED.brick)
  chimney.rotation.x = Math.PI / 2
  chimney.position.set(b.x1 - 6, b.y1 - 6, 13)
  parts.push(chimney)
  parts.push(box(6, 3, 4.5, SHARED.dark, b.cx, b.y0 - 0.1, 2.25)) // gate
  return [parts, h + 3]
}

/** Административный корпус: glass with floor slabs and rooftop plant. */
function office(ring: [number, number][], walls: THREE.MeshLambertMaterial, floors: number): [THREE.Object3D[], number] {
  const b = bbox(ring)
  const fh = 3.6
  const h = floors * fh
  const parts: THREE.Object3D[] = [extrude(ring, h, walls)]
  for (let f = 1; f <= floors; f++) parts.push(box(b.w + 0.4, b.d + 0.4, 0.45, SHARED.white, b.cx, b.cy, f * fh))
  parts.push(box(b.w * 0.25, b.d * 0.3, 2, SHARED.steel, b.cx + b.w * 0.2, b.cy, h + 1))
  return [parts, h + 2]
}

/** Гараж / ремзона: low box with roll-up doors. */
function garage(ring: [number, number][], walls: THREE.MeshLambertMaterial): [THREE.Object3D[], number] {
  const b = bbox(ring)
  const h = 7
  const parts: THREE.Object3D[] = [extrude(ring, h, walls), box(b.w + 0.6, b.d + 0.6, 0.4, SHARED.roof, b.cx, b.cy, h)]
  for (const x of spread(b.x0, b.x1, 10, 3)) parts.push(box(5, 0.3, 5, SHARED.concrete, x, b.y0 - 0.1, 2.5))
  return [parts, h]
}

const WALL_COLOR: Record<Building['building_type'], string> = {
  warehouse: '#cbd5e1',
  production: '#d6c7b0',
  office: '#5b7fa6',
  garage: '#94a3b8',
}

export function buildingModel(bld: Building): BuildingModel {
  const ring = bld.geometry.coordinates[0] as [number, number][]
  const walls = lambert(WALL_COLOR[bld.building_type])
  const [parts, height] =
    bld.building_type === 'warehouse'
      ? warehouse(ring, walls)
      : bld.building_type === 'production'
        ? production(ring, walls)
        : bld.building_type === 'office'
          ? office(ring, walls, bld.floors)
          : garage(ring, walls)
  const group = new THREE.Group()
  group.add(...parts)
  group.userData.pick = { kind: 'building', id: bld.id } satisfies Pickable
  return { group, walls: [walls], height }
}

export interface VehicleModel {
  group: THREE.Group
  /** Body paint: shows the live status. */
  paint: THREE.MeshLambertMaterial
}

/** Models face +y (heading 0). Sizes are real, the scene scales them up when zoomed out. */
export function vehicleModel(v: Pick<Vehicle, 'id' | 'kind'>): VehicleModel {
  const paint = lambert(STATUS_COLOR.stopped)
  const g = new THREE.Group()
  if (v.kind === 'truck') {
    g.add(
      box(2.5, 13.2, 0.5, SHARED.dark, 0, -0.4, 1.1), // chassis
      box(2.55, 9.5, 3.0, SHARED.white, 0, -1.9, 2.85), // trailer
      box(2.45, 2.4, 2.7, paint, 0, 4.7, 2.5), // cab
      box(2.2, 0.1, 1.0, SHARED.glass, 0, 5.92, 3.2), // windshield
      wheel(0.55, 2.6, 0, 4.4),
      wheel(0.55, 2.6, 0, -3.6),
      wheel(0.55, 2.6, 0, -5.0),
    )
  } else if (v.kind === 'loader') {
    g.add(
      box(1.3, 2.4, 1.1, paint, 0, -0.2, 0.95),
      box(1.2, 1.1, 1.0, SHARED.glass, 0, -0.4, 2.0), // cage
      box(0.12, 0.12, 3.6, SHARED.steel, -0.45, 1.05, 1.8), // mast
      box(0.12, 0.12, 3.6, SHARED.steel, 0.45, 1.05, 1.8),
      box(0.15, 1.2, 0.08, SHARED.steel, -0.35, 1.7, 0.2), // forks
      box(0.15, 1.2, 0.08, SHARED.steel, 0.35, 1.7, 0.2),
      wheel(0.35, 1.4, 0, 0.7),
      wheel(0.3, 1.4, 0, -1.0),
    )
  } else {
    g.add(
      box(1.8, 4.4, 0.8, paint, 0, 0, 0.75),
      box(1.6, 2.3, 0.65, SHARED.glass, 0, -0.2, 1.45),
      wheel(0.35, 1.9, 0, 1.4),
      wheel(0.35, 1.9, 0, -1.4),
    )
  }
  g.userData.pick = { kind: 'vehicle', id: v.id } satisfies Pickable
  return { group: g, paint }
}

/** Flat ring on the ground under the selected object. */
export function selectionRing(): THREE.Mesh {
  const m = new THREE.Mesh(
    new THREE.RingGeometry(0.93, 1, 64),
    new THREE.MeshBasicMaterial({ color: '#38bdf8', transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }),
  )
  m.position.z = 0.3
  m.visible = false
  return m
}

export { bbox }

// --- x-ray view of a building: the Sims "walls down" look -------------------------------------------

/** Storey height by building type: the shell of the normal model is floors × this. */
export const FLOOR_H: Record<Building['building_type'], number> = { office: 3.6, production: 4.5, warehouse: 10, garage: 7 }
export const floorBase = (b: Building, floor: number) => (Math.max(1, floor) - 1) * FLOOR_H[b.building_type]

export const SENSOR_STATUS_COLOR: Record<string, string> = {
  ok: '#22c55e',
  warning: '#f59e0b',
  critical: '#ef4444',
  offline: '#64748b',
  unknown: '#64748b',
}

/** Glass shell of the whole building with crisp edges, so the floor inside stays readable. */
export function xrayShell(b: Building): THREE.Group {
  const ring = b.geometry.coordinates[0] as [number, number][]
  const h = b.floors * FLOOR_H[b.building_type]
  const g = new THREE.Group()
  const shell = extrude(ring, h, new THREE.MeshLambertMaterial({ color: '#94a3b8', transparent: true, opacity: 0.1, depthWrite: false }))
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(shell.geometry),
    new THREE.LineBasicMaterial({ color: '#7dd3fc', transparent: true, opacity: 0.55 }),
  )
  g.add(shell, edges)
  return g
}

/** One storey: the slab, each room tinted by its worst sensor, knee-high walls along the rooms. */
export function storey(b: Building, floor: number, rooms: Room[], roomColor: (id: string) => string): THREE.Group {
  const ring = b.geometry.coordinates[0] as [number, number][]
  const z = floorBase(b, floor)
  const g = new THREE.Group()
  g.add(extrude(ring, 0.3, lambert('#334155'), z))
  for (const r of rooms) {
    const rr = r.geometry.coordinates[0] as [number, number][]
    const slab = extrude(rr, 0.15, new THREE.MeshLambertMaterial({ color: roomColor(r.id), transparent: true, opacity: 0.55 }), z + 0.3)
    g.add(slab)
    // walls: a thin band along the room outline, the Sims cutaway height
    for (let i = 0; i < rr.length - 1; i++) {
      const [x0, y0] = rr[i]
      const [x1, y1] = rr[i + 1]
      const len = Math.hypot(x1 - x0, y1 - y0)
      const wall = box(len, 0.35, 1.4, SHARED.white, (x0 + x1) / 2, (y0 + y1) / 2, z + 0.3 + 0.7)
      wall.rotation.z = Math.atan2(y1 - y0, x1 - x0)
      g.add(wall)
    }
  }
  return g
}

/** A sensor pin: a post with a glowing head, coloured by status; picked as the sensor. */
export function sensorPin(s: Sensor, z: number, color: string): THREE.Group {
  const g = new THREE.Group()
  const mat = new THREE.MeshLambertMaterial({ color, emissive: color, emissiveIntensity: 0.45 })
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 2.6, 8), SHARED.steel)
  post.rotation.x = Math.PI / 2
  post.position.z = 1.3
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.9, 16, 12), mat)
  head.position.z = 3.2
  g.add(post, head)
  g.position.set(s.geo?.x ?? 0, s.geo?.y ?? 0, z)
  g.userData.pick = { kind: 'sensor', id: s.id } satisfies Pickable
  g.userData.head = mat
  return g
}
