import type { components } from './schema'

type S = components['schemas']

export type ObjectsResponse = S['ObjectsResponse']
export type Building = S['Building']
export type Room = S['Room']
export type Road = S['Road']
export type Geozone = S['Geozone']
export type Checkpoint = S['Checkpoint']
export type Sensor = S['Sensor']
export type SensorCreate = S['SensorCreate']
export type SensorTypeInfo = S['SensorTypeInfo']
export type Threshold = S['Threshold']
export type Vehicle = S['Vehicle']
export type SensorLive = S['SensorLive']
export type VehicleLive = S['VehicleLive']
export type ZoneOccupancy = S['ZoneOccupancy']
export type LiveState = S['LiveState']
export type Alert = S['Alert']
export type AlertPage = S['AlertPage']
export type SensorHistory = S['SensorHistory']
export type VehicleRoute = S['VehicleRoute']
export type RoutePoint = S['RoutePoint']
export type BulkResult = S['BulkResult']
export type KpiResponse = S['KpiResponse']
export type Heatmap = S['Heatmap']
export type Replay = S['Replay']
export type TokenPair = S['TokenPair']
export type User = S['User']
export type WhitelistPage = S['WhitelistPage']
export type ApiKey = S['ApiKey']
export type ApiKeyIssued = S['ApiKeyIssued']
export type Endpoints = S['Endpoints']
export type Rejection = S['Rejection']

/** GET /adapters of the connectors service (not part of the API contract). */
export interface AdapterInfo {
  name: string
  sensor_type: SensorType | null
  title: string
  http: string
  mqtt_topic: string
  example: Record<string, unknown>
  device_field: string
  ts_field: string
  ts_format: 'iso' | 'unix_s' | 'unix_ms'
  schema: { properties?: Record<string, JsonSchemaProp>; required?: string[]; description?: string }
}
export interface JsonSchemaProp {
  type?: string
  anyOf?: { type?: string; enum?: unknown[]; const?: unknown }[]
  enum?: unknown[]
  const?: unknown
  description?: string
  format?: string
  minimum?: number
  maximum?: number
}
export type Severity = Alert['severity']
export type SensorType = Sensor['type']

export type Layer = 'sensors' | 'vehicles' | 'people' | 'alerts'

/** Messages of WS /ws/live (contracts/ws.schema.json). */
interface WsBase {
  ts: string
  building_id: string | null
}
export type WsMessage =
  | (WsBase & { type: 'sensor'; data: SensorLive })
  | (WsBase & { type: 'vehicle'; data: VehicleLive })
  | (WsBase & { type: 'zone'; data: ZoneOccupancy })
  | (WsBase & { type: 'alert'; data: Alert })

export interface WsSubscribe {
  op: 'subscribe'
  buildings?: string[] | null
  layers?: Layer[] | null
  sensor_types?: SensorType[] | null
}
