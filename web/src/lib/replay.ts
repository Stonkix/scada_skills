import type { RoutePoint } from '@/api/types'

export interface Sample {
  x: number
  y: number
  speed: number
  heading: number
}

/** Pre-parsed track: timestamps as numbers so scrubbing does no Date parsing. */
export interface Track {
  id: string
  t: Float64Array
  points: RoutePoint[]
}

export function toTrack(id: string, points: RoutePoint[]): Track {
  return { id, points, t: Float64Array.from(points, (p) => Date.parse(p.ts)) }
}

/** Last index with t[i] <= time (binary search); -1 if before the first sample. */
export function indexAt(t: Float64Array, time: number): number {
  let lo = 0
  let hi = t.length - 1
  if (hi < 0 || time < t[0]) return -1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (t[mid] <= time) lo = mid
    else hi = mid - 1
  }
  return lo
}

const headingOf = (dx: number, dy: number) => ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360

/**
 * Position at `time`, linearly interpolated between samples. Gaps longer than
 * `maxGapMs` are not bridged (the tracker was off or the vehicle left the site),
 * and nothing is returned outside the recorded span.
 */
export function positionAt(track: Track, time: number, maxGapMs = 30_000): Sample | null {
  const i = indexAt(track.t, time)
  if (i < 0) return null
  const a = track.points[i]
  const b = track.points[i + 1]
  if (!b) return time - track.t[i] <= maxGapMs ? { x: a.x, y: a.y, speed: a.speed_kmh, heading: 0 } : null
  const span = track.t[i + 1] - track.t[i]
  if (span > maxGapMs) return time - track.t[i] <= maxGapMs ? { x: a.x, y: a.y, speed: a.speed_kmh, heading: 0 } : null
  const k = span > 0 ? (time - track.t[i]) / span : 0
  const dx = b.x - a.x
  const dy = b.y - a.y
  return {
    x: a.x + dx * k,
    y: a.y + dy * k,
    speed: a.speed_kmh + (b.speed_kmh - a.speed_kmh) * k,
    heading: Math.hypot(dx, dy) > 0.2 ? headingOf(dx, dy) : NaN,
  }
}

/** Points of the trail behind the vehicle over the last `windowMs`. */
export function trail(track: Track, time: number, windowMs: number): [number, number][] {
  const end = indexAt(track.t, time)
  if (end < 0) return []
  const start = Math.max(0, indexAt(track.t, time - windowMs))
  const out: [number, number][] = []
  for (let i = start; i <= end; i++) out.push([track.points[i].x, track.points[i].y])
  const now = positionAt(track, time)
  if (now) out.push([now.x, now.y])
  return out
}
