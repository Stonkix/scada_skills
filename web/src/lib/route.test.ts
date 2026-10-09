import { describe, expect, it } from 'vitest'
import { length, progress, project, slice, type Pt } from './route'

const L: Pt[] = [
  [0, 0],
  [100, 0],
  [100, 50],
]

describe('route', () => {
  it('measures a polyline', () => {
    expect(length(L)).toBe(150)
  })

  it('projects a point onto the closest segment', () => {
    expect(project(L, [40, 3])).toMatchObject({ along: 40, total: 150, offset: 3, point: [40, 0] })
    expect(project(L, [104, 20])).toMatchObject({ along: 120, offset: 4 })
  })

  it('clamps beyond the ends', () => {
    expect(project(L, [-30, 0]).along).toBe(0)
    expect(project(L, [100, 90]).along).toBe(150)
  })

  it('slices the remaining part', () => {
    expect(slice(L, 40)).toEqual([[40, 0], [100, 0], [100, 50]])
    expect(slice(L, 40, 120)).toEqual([[40, 0], [100, 0], [100, 20]])
  })

  it('counts progress from the trip origin in either direction', () => {
    expect(progress(L, [40, 0], false)).toMatchObject({ done: 40, left: 110 })
    expect(progress(L, [40, 0], true)).toMatchObject({ done: 110, left: 40 })
  })
})
