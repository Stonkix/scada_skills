import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import type { AdapterInfo, Endpoints } from '@/api/types'
import { curlCommand, mqttCommand, samplePayload, schemaRows, shellQuote } from './snippets'

const climate: AdapterInfo = {
  name: 'climate',
  sensor_type: 'climate',
  title: 'Климат',
  http: 'POST /ingest/climate',
  mqtt_topic: 'sensors/climate/{device_id}',
  example: { device: 'clim-x', temperature: 39.6, unit: 'F', humidity: 61.2, ts_ms: 1 },
  device_field: 'device',
  ts_field: 'ts_ms',
  ts_format: 'unix_ms',
  schema: {
    properties: {
      device: { type: 'string' },
      unit: { enum: ['C', 'F'] },
      humidity: { type: 'number', minimum: 0, maximum: 100 },
      note: { anyOf: [{ type: 'string' }, { type: 'null' }], description: 'optional' },
    },
    required: ['device', 'humidity'],
  },
}
const endpoints: Endpoints = {
  http_base: 'http://host:8001', mqtt_host: 'host', mqtt_port: 1883, api_key_header: 'X-API-Key', mqtt_key_property: 'x-api-key',
}

describe('samplePayload', () => {
  it('puts the chosen device and now in the adapter format, leaving the example intact', () => {
    const p = samplePayload(climate, 'clim-wh1-dock', 1_791_450_000_123)
    expect(p).toMatchObject({ device: 'clim-wh1-dock', ts_ms: 1_791_450_000_123, temperature: 39.6 })
    expect(climate.example.device).toBe('clim-x')
    const gnss = { ...climate, ts_field: 'fix_time', ts_format: 'unix_s' as const, example: { fix_time: 0 } }
    expect(samplePayload(gnss, null, 1_791_450_000_123).fix_time).toBe(1_791_450_000.12)
    expect(samplePayload(reactive(structuredClone(climate)), 'clim-1').device).toBe('clim-1') // catalogue lives in a ref
    const iso = { ...climate, ts_field: 'ts', ts_format: 'iso' as const, example: {} }
    expect(samplePayload(iso, null, Date.UTC(2026, 9, 8)).ts).toBe('2026-10-08T00:00:00.000Z')
  })
})

describe('shell snippets', () => {
  it('quotes payloads with single quotes inside', () => {
    expect(shellQuote(`a'b`)).toBe(`'a'\\''b'`)
    const cmd = curlCommand(endpoints, climate, 'sk_123', { name: `O'Brien` })
    expect(cmd).toContain(`-d '{"name":"O'\\''Brien"}'`)
    expect(cmd).toContain('-H "X-API-Key: sk_123"')
    expect(cmd.split('\n')[0]).toBe('curl -X POST http://host:8001/ingest/climate \\')
  })

  it('builds an MQTT 5 publish with the key as a user property', () => {
    const cmd = mqttCommand(endpoints, climate, 'sk_123', 'clim-wh1-dock', { a: 1 })
    expect(cmd).toContain('-V mqttv5 -h host -p 1883')
    expect(cmd).toContain(`-t 'sensors/climate/clim-wh1-dock'`)
    expect(cmd).toContain(`-D publish user-property x-api-key 'sk_123'`)
  })
})

describe('schemaRows', () => {
  it('describes types, enums, optionality and ranges', () => {
    const rows = Object.fromEntries(schemaRows(climate).map((r) => [r.name, r]))
    expect(rows.unit.type).toBe('"C" | "F"')
    expect(rows.note.type).toBe('string | null')
    expect(rows.humidity).toMatchObject({ required: true, description: '≥ 0, ≤ 100' })
    expect(rows.note.required).toBe(false)
  })
})
