import type { ObjectsResponse, Trip } from '@/api/types'

/** One thing the map search can find. `room` opens its building on the room's floor. */
export interface SearchItem {
  kind: 'site' | 'building' | 'room' | 'sensor' | 'vehicle'
  id: string
  title: string
  subtitle: string
  /** For rooms: the building to open and the floor. */
  building_id?: string
  floor?: number
  haystack: string
}

export const KIND_LABEL: Record<SearchItem['kind'], string> = {
  site: 'Площадка',
  building: 'Здание',
  room: 'Помещение',
  sensor: 'Датчик',
  vehicle: 'Техника',
}

// plates are typed in Latin as often as in Cyrillic: А123ВС77 == A123BC77
const LOOKALIKES: Record<string, string> = { a: 'а', b: 'в', e: 'е', k: 'к', m: 'м', h: 'н', o: 'о', p: 'р', c: 'с', t: 'т', y: 'у', x: 'х' }

export function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[abekmhopctyx]/g, (ch) => LOOKALIKES[ch])
    .replace(/[\s«»"'.,№-]+/g, ' ')
    .trim()
}

/** Plates without spaces too: «а 123 вс» finds А123ВС77. */
const squash = (s: string) => normalize(s).replace(/ /g, '')

export function buildIndex(o: ObjectsResponse, trips: Trip[] = []): SearchItem[] {
  const siteName = new Map(o.sites.map((s) => [s.id, s.name]))
  const buildingName = new Map(o.buildings.map((b) => [b.id, b.name]))
  const tripOf = new Map(trips.map((t) => [t.vehicle_id, t]))
  const item = (i: Omit<SearchItem, 'haystack'>, ...extra: (string | null | undefined)[]): SearchItem => {
    const text = [i.title, i.subtitle, i.id, ...extra].filter(Boolean).join(' ')
    return { ...i, haystack: `${normalize(text)} ${squash(text)}` }
  }
  return [
    ...o.sites.map((s) => item({ kind: 'site', id: s.id, title: s.name, subtitle: s.address ?? '' })),
    ...o.buildings.map((b) =>
      item({ kind: 'building', id: b.id, title: b.name, subtitle: siteName.get(b.site_id ?? '') ?? '' }),
    ),
    ...o.rooms.map((r) =>
      item({
        kind: 'room', id: r.id, title: r.name, subtitle: `${buildingName.get(r.building_id) ?? ''}, ${r.floor} эт.`,
        building_id: r.building_id, floor: r.floor,
      }),
    ),
    ...o.sensors
      .filter((s) => s.enabled && !s.vehicle_id)
      .map((s) => item({ kind: 'sensor', id: s.id, title: s.name, subtitle: buildingName.get(s.building_id ?? '') ?? '' })),
    ...o.vehicles.map((v) => {
      const t = tripOf.get(v.id)
      const subtitle = t
        ? `${t.cargo} → ${siteName.get(t.destination_site_id) ?? t.destination_site_id}`
        : [v.model, v.carrier].filter(Boolean).join(' · ')
      return item({ kind: 'vehicle', id: v.id, title: v.plate, subtitle }, v.model, v.carrier, t?.id, t?.cargo, t?.driver_name,
        t && siteName.get(t.origin_site_id))
    }),
  ]
}

const KIND_ORDER: SearchItem['kind'][] = ['vehicle', 'site', 'building', 'room', 'sensor']

/** Every word must match; title prefix matches first, then title matches, then the rest. */
export function search(index: SearchItem[], query: string, limit = 12): SearchItem[] {
  const words = normalize(query).split(' ').filter(Boolean)
  if (!words.length) return []
  const scored: { item: SearchItem; score: number }[] = []
  for (const item of index) {
    if (!words.every((w) => item.haystack.includes(w))) continue
    const title = normalize(item.title)
    const titleSq = squash(item.title)
    const first = words[0]
    const score =
      (title.startsWith(first) || titleSq.startsWith(first) ? 0 : title.includes(first) || titleSq.includes(first) ? 1 : 2) * 10 +
      KIND_ORDER.indexOf(item.kind)
    scored.push({ item, score })
  }
  return scored.sort((a, b) => a.score - b.score || a.item.title.localeCompare(b.item.title, 'ru')).slice(0, limit).map((s) => s.item)
}
