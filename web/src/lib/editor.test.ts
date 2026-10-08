import { describe, expect, it } from 'vitest'
import { diff, inPolygon, locate, newRoom, newSensor, nextId, relocateSensors, validate, type Feature, type Layout, type Pt } from './editor'

const rect = (x0: number, y0: number, x1: number, y1: number): Pt[][] => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]
const f = (id: string, kind: string, coordinates: unknown, props: Record<string, unknown> = {}): Feature => ({
  type: 'Feature', id, geometry: { type: Array.isArray((coordinates as unknown[])[0]) ? 'Polygon' : 'Point', coordinates } as Feature['geometry'],
  properties: { id, kind, name: id, ...props },
})

function plan(): Layout {
  return {
    type: 'FeatureCollection',
    metadata: { units: 'm', extent: [0, 0, 800, 500], georef: { origin_lat: 55.7, origin_lon: 37.4, rotation_deg: 0 } },
    features: [
      f('b-wh1', 'building', rect(200, 300, 360, 400), { building_type: 'warehouse', floors: 1 }),
      f('r-wh1-dock', 'room', rect(200, 300, 360, 330), { building_id: 'b-wh1', floor: 1, room_type: 'dock' }),
      f('z-gate', 'geozone', rect(0, 230, 40, 270), { zone_type: 'gate' }),
      f('z-site', 'geozone', rect(0, 0, 800, 500), { zone_type: 'speed' }),
      f('clim-wh1-dock', 'sensor', [250, 315], { sensor_type: 'climate', building_id: 'b-wh1', zone_id: 'r-wh1-dock', floor: 1 }),
    ],
  }
}

describe('geometry', () => {
  it('counts edge points as inside (entrance readers sit on walls)', () => {
    const ring = rect(0, 0, 10, 10)[0]
    expect(inPolygon([5, 5], ring)).toBe(true)
    expect(inPolygon([10, 5], ring)).toBe(true)
    expect(inPolygon([11, 5], ring)).toBe(false)
  })

  it('locates rooms, buildings and outdoor zones, never the speed zone', () => {
    const l = plan()
    expect(locate(l, [250, 315], 1)).toEqual({ building_id: 'b-wh1', zone_id: 'r-wh1-dock', floor: 1 })
    expect(locate(l, [250, 380], 1)).toEqual({ building_id: 'b-wh1', zone_id: 'b-wh1', floor: 1 })
    expect(locate(l, [20, 250], 1)).toEqual({ building_id: null, zone_id: 'z-gate', floor: null })
    expect(locate(l, [500, 100], 1).zone_id).toBeNull()
  })
})

describe('creating objects', () => {
  it('names new sensors after their zone with unique ids', () => {
    const l = plan()
    const s = newSensor(l, 'climate', [300, 320], 1, new Set(['clim-wh1-dock']), 'Климат')
    expect(s.id).toBe('clim-wh1-dock-2')
    expect(s.properties).toMatchObject({ sensor_type: 'climate', zone_id: 'r-wh1-dock', building_id: 'b-wh1', floor: 1 })
    expect(nextId('Mot Склад №1', new Set())).toBe('mot-1') // Cyrillic dropped, digits kept
  })

  it('creates rooms only fully inside a building', () => {
    const l = plan()
    const room = newRoom(l, [210, 340], [260, 390], 1, new Set())
    expect(typeof room).not.toBe('string')
    expect((room as Feature).properties).toMatchObject({ building_id: 'b-wh1', floor: 1, kind: 'room' })
    expect(newRoom(l, [190, 340], [260, 390], 1, new Set())).toMatch(/внутри здания/)
    expect(newRoom(l, [210, 340], [211, 341], 1, new Set())).toMatch(/3×3/)
    expect(newRoom(l, [210, 340], [260, 390], 2, new Set())).toMatch(/1 эт/)
  })

  it('moves only sensors strictly inside a new room; wall-mounted ones keep their binding', () => {
    const l = plan()
    l.features.push(
      f('acs-wh1', 'sensor', [290, 300], { sensor_type: 'access_control', building_id: 'b-wh1', zone_id: 'b-wh1', floor: 1 }),
      f('mot-in', 'sensor', [250, 360], { sensor_type: 'motion', building_id: 'b-wh1', zone_id: 'b-wh1', floor: 1 }),
    )
    const room = newRoom(l, [240, 340], [300, 390], 1, new Set()) as Feature
    l.features.push(room)
    expect(relocateSensors(l, room)).toBe(1)
    expect(l.features.find((x) => x.id === 'mot-in')!.properties.zone_id).toBe(room.id)
    expect(l.features.find((x) => x.id === 'acs-wh1')!.properties.zone_id).toBe('b-wh1')
    expect(l.features.find((x) => x.id === 'clim-wh1-dock')!.properties.zone_id).toBe('r-wh1-dock')
  })

  it('re-points sensors when their room disappears', () => {
    const l = plan()
    l.features = l.features.filter((x) => x.id !== 'r-wh1-dock')
    expect(relocateSensors(l)).toBe(1)
    expect(l.features.find((x) => x.id === 'clim-wh1-dock')!.properties.zone_id).toBe('b-wh1')
  })
})

describe('save checks', () => {
  it('catches duplicates, bad ids and sensors off site', () => {
    const l = plan()
    l.features.push(f('clim-wh1-dock', 'sensor', [1, 1], { sensor_type: 'climate' }), f('Bad Id', 'sensor', [900, 1], { sensor_type: 'motion' }))
    const errs = validate(l)
    expect(errs.some((e) => e.includes('clim-wh1-dock встречается 2'))).toBe(true)
    expect(errs.some((e) => e.startsWith('Bad Id: id'))).toBe(true)
    expect(errs.some((e) => e.includes('за пределами'))).toBe(true)
  })

  it('summarises the change', () => {
    const a = plan()
    const b = plan()
    b.features.push(newSensor(b, 'motion', [300, 380], 1, new Set(), 'Движение'))
    ;(b.features[4].geometry.coordinates as Pt)[0] = 260
    b.features = b.features.filter((x) => x.id !== 'r-wh1-dock')
    expect(diff(a, b)).toEqual({ sensors: { added: 1, removed: 0, changed: 1 }, rooms: { added: 0, removed: 1, changed: 0 } })
  })
})
