import { describe, expect, it } from 'vitest'
import { indexAt, positionAt, toTrack, trail } from './replay'

const T0 = Date.parse('2026-10-08T10:00:00Z')
const at = (s: number) => new Date(T0 + s * 1000).toISOString()
const track = toTrack('v', [
  { ts: at(0), x: 0, y: 0, speed_kmh: 10 },
  { ts: at(10), x: 100, y: 0, speed_kmh: 20 },
  { ts: at(20), x: 100, y: 50, speed_kmh: 20 },
  { ts: at(200), x: 300, y: 50, speed_kmh: 0 }, // 180 s gap: tracker was off
])

describe('indexAt', () => {
  it('finds the last sample not after the time', () => {
    expect(indexAt(track.t, T0 - 1)).toBe(-1)
    expect(indexAt(track.t, T0)).toBe(0)
    expect(indexAt(track.t, T0 + 9999)).toBe(0)
    expect(indexAt(track.t, T0 + 10000)).toBe(1)
    expect(indexAt(track.t, T0 + 10 ** 9)).toBe(3)
  })
})

describe('positionAt', () => {
  it('interpolates position, speed and heading', () => {
    const p = positionAt(track, T0 + 5000)!
    expect(p.x).toBeCloseTo(50)
    expect(p.y).toBe(0)
    expect(p.speed).toBeCloseTo(15)
    expect(p.heading).toBeCloseTo(90) // east
    expect(positionAt(track, T0 + 15000)!.heading).toBeCloseTo(0) // north
  })

  it('does not bridge long gaps or extrapolate', () => {
    expect(positionAt(track, T0 - 1000)).toBeNull()
    expect(positionAt(track, T0 + 25000)).toEqual({ x: 100, y: 50, speed: 20, heading: 0 })
    expect(positionAt(track, T0 + 100000)).toBeNull()
    expect(positionAt(track, T0 + 260000)).toBeNull()
  })
})

describe('trail', () => {
  it('ends at the interpolated current position', () => {
    const pts = trail(track, T0 + 15000, 12000)
    expect(pts[0]).toEqual([0, 0])
    expect(pts.at(-1)).toEqual([100, 25])
  })
})
