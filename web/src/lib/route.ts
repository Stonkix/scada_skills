/** Polyline geometry in plan metres: where a vehicle is along its route. */

export type Pt = [number, number]

export function length(line: Pt[]): number {
  let total = 0
  for (let i = 1; i < line.length; i++) total += Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1])
  return total
}

export interface Projection {
  /** Metres from the start of the line to the closest point. */
  along: number
  total: number
  /** Distance from the point to the line. */
  offset: number
  point: Pt
}

/** Closest point of the line to p. */
export function project(line: Pt[], p: Pt): Projection {
  let best: Projection = { along: 0, total: 0, offset: Infinity, point: line[0] }
  let walked = 0
  for (let i = 1; i < line.length; i++) {
    const [ax, ay] = line[i - 1]
    const [bx, by] = line[i]
    const dx = bx - ax
    const dy = by - ay
    const seg = Math.hypot(dx, dy)
    const t = seg ? Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (seg * seg))) : 0
    const q: Pt = [ax + dx * t, ay + dy * t]
    const offset = Math.hypot(p[0] - q[0], p[1] - q[1])
    if (offset < best.offset) best = { along: walked + seg * t, total: 0, offset, point: q }
    walked += seg
  }
  return { ...best, total: walked }
}

/** The part of the line from `from` metres to `to` metres along it. */
export function slice(line: Pt[], from: number, to = Infinity): Pt[] {
  const out: Pt[] = []
  let walked = 0
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1]
    const b = line[i]
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1])
    const at = (d: number): Pt => {
      const t = seg ? (d - walked) / seg : 0
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
    }
    if (walked + seg >= from && walked <= to) {
      if (!out.length) out.push(from > walked ? at(from) : a)
      out.push(walked + seg > to ? at(to) : b)
    }
    walked += seg
  }
  return out
}

/** Progress of a trip on a route drawn in either direction: 0 at origin, 1 at destination. */
export function progress(line: Pt[], p: Pt, reversed: boolean): { done: number; left: number; total: number; offset: number } {
  const pr = project(line, p)
  const done = reversed ? pr.total - pr.along : pr.along
  return { done, left: pr.total - done, total: pr.total, offset: pr.offset }
}
