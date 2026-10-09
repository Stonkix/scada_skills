import { describe, expect, it } from 'vitest'
import type { ObjectsResponse, Trip } from '@/api/types'
import { buildIndex, normalize, search } from './search'

const rect = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]] as [number, number][][]
const objects = {
  sites: [
    { id: 's-podolsk', name: 'Завод «Подольск»', site_type: 'plant', address: 'Подольск, промзона', geometry: { type: 'Polygon', coordinates: rect } },
    { id: 's-chekhov', name: 'Холодильный склад «Чехов»', site_type: 'cold_store', address: 'Чехов', geometry: { type: 'Polygon', coordinates: rect } },
  ],
  buildings: [{ id: 'b-wh2', site_id: 's-podolsk', name: 'Склад №2 (холодный)', building_type: 'warehouse', floors: 1, geometry: { type: 'Polygon', coordinates: rect } }],
  rooms: [{ id: 'r-wh2-dock', name: 'Склад №2: зона отгрузки', building_id: 'b-wh2', floor: 1, room_type: 'dock', geometry: { type: 'Polygon', coordinates: rect } }],
  sensors: [
    { id: 'clim-wh2-dock', name: 'Климат, склад №2: отгрузка', type: 'climate', enabled: true, building_id: 'b-wh2', vehicle_id: null },
    { id: 'gnss-truck-1', name: 'Трекер', type: 'gnss', enabled: true, building_id: null, vehicle_id: 'v-truck-1' },
  ],
  vehicles: [
    { id: 'v-truck-1', plate: 'А123ВС77', kind: 'truck', model: 'КАМАЗ 65115', carrier: 'ООО «ТрансЛогистик»', sensor_id: 'gnss-truck-1' },
    { id: 'v-truck-9', plate: 'У357ВА50', kind: 'truck', model: 'MAN TGX', carrier: 'ООО «ХолодТранс»', sensor_id: 'gnss-truck-9' },
  ],
} as unknown as ObjectsResponse
const trips = [
  { id: 'ПЛ-261009-000042', vehicle_id: 'v-truck-9', origin_site_id: 's-chekhov', destination_site_id: 's-podolsk', cargo: 'Замороженная продукция', driver_name: 'С***в А. В.' },
] as unknown as Trip[]
const index = buildIndex(objects, trips)
const ids = (q: string) => search(index, q).map((i) => i.id)

describe('search', () => {
  it('finds a plate typed in Latin, with spaces, lowercase', () => {
    expect(normalize('A123BC77')).toBe('а123вс77')
    expect(ids('a123bc')).toEqual(['v-truck-1'])
    expect(ids('а 123 вс')).toEqual(['v-truck-1'])
  })

  it('finds vehicles by their trip: cargo, waybill, destination', () => {
    expect(ids('заморож')).toEqual(['v-truck-9'])
    expect(ids('ПЛ-261009-000042')).toEqual(['v-truck-9'])
    expect(ids('холодтранс')).toEqual(['v-truck-9'])
  })

  it('needs every word and ranks title prefixes first', () => {
    expect(ids('склад холодный')).toEqual(['b-wh2', 'r-wh2-dock', 'clim-wh2-dock']) // the building first, then what is inside
    expect(ids('склад отгруз')).toEqual(['r-wh2-dock', 'clim-wh2-dock'])
  })

  it('keeps rooms pointing at their building and floor; skips vehicle trackers', () => {
    expect(search(index, 'зона отгрузки')[0]).toMatchObject({ kind: 'room', building_id: 'b-wh2', floor: 1 })
    expect(ids('трекер')).toEqual([])
    expect(ids('чехов')).toEqual(['s-chekhov', 'v-truck-9']) // a title match beats a trip match
  })
})
