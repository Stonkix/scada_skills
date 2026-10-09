import { describe, expect, it } from 'vitest'
import { M_PER_DEG_LAT, toLngLat, toLocal } from './geo'

const g = { origin_lat: 55.7, origin_lon: 37.4, rotation_deg: 0 }

describe('geo', () => {
  it('maps the plan origin to the georef origin', () => {
    expect(toLngLat(g, 0, 0)).toEqual([37.4, 55.7])
  })

  it('plan +y is north and +x is east without rotation', () => {
    const [lng, lat] = toLngLat(g, 0, 100)
    expect(lng).toBeCloseTo(37.4, 10)
    expect((lat - 55.7) * M_PER_DEG_LAT).toBeCloseTo(100, 6)
    const [lng2, lat2] = toLngLat(g, 100, 0)
    expect(lat2).toBeCloseTo(55.7, 10)
    expect(lng2).toBeGreaterThan(37.4)
  })

  it('rotation turns plan north clockwise', () => {
    const r = { ...g, rotation_deg: 90 }
    const [lng, lat] = toLngLat(r, 0, 100) // plan north now points east
    expect(lat).toBeCloseTo(55.7, 8)
    expect(lng).toBeGreaterThan(37.4)
  })

  it('round-trips', () => {
    const r = { ...g, rotation_deg: 23 }
    const [x, y] = toLocal(r, ...toLngLat(r, 412.5, -37.25))
    expect(x).toBeCloseTo(412.5, 6)
    expect(y).toBeCloseTo(-37.25, 6)
  })
})
