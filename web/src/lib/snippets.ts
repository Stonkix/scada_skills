import type { AdapterInfo, Endpoints } from '@/api/types'

/** The adapter's example with a real device id and "now" in the adapter's time format. */
export function samplePayload(a: AdapterInfo, deviceId: string | null, now = Date.now()): Record<string, unknown> {
  // JSON round-trip, not structuredClone: the catalogue sits in a Vue ref and Proxies are not cloneable
  const p = JSON.parse(JSON.stringify(a.example)) as Record<string, unknown>
  if (deviceId) p[a.device_field] = deviceId
  p[a.ts_field] =
    a.ts_format === 'unix_ms' ? now : a.ts_format === 'unix_s' ? Math.round(now / 10) / 100 : new Date(now).toISOString()
  return p
}

/** Single-quote for POSIX shells: ' -> '\'' */
export const shellQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`

export function curlCommand(e: Endpoints, a: AdapterInfo, key: string, payload: unknown): string {
  return [
    `curl -X POST ${e.http_base}/ingest/${a.name}`,
    `  -H "${e.api_key_header}: ${key}"`,
    `  -H "Content-Type: application/json"`,
    `  -d ${shellQuote(JSON.stringify(payload))}`,
  ].join(' \\\n')
}

export function mqttCommand(e: Endpoints, a: AdapterInfo, key: string, deviceId: string, payload: unknown): string {
  return [
    `mosquitto_pub -V mqttv5 -h ${e.mqtt_host} -p ${e.mqtt_port}`,
    `  -t ${shellQuote(`sensors/${a.name}/${deviceId}`)}`,
    `  -D publish user-property ${e.mqtt_key_property} ${shellQuote(key)}`,
    `  -m ${shellQuote(JSON.stringify(payload))}`,
  ].join(' \\\n')
}

export function pythonSnippet(e: Endpoints, a: AdapterInfo, key: string, payload: unknown): string {
  return `import requests  # pip install requests

payload = ${JSON.stringify(payload, null, 4).replace(/\btrue\b/g, 'True').replace(/\bfalse\b/g, 'False').replace(/\bnull\b/g, 'None')}

r = requests.post(
    "${e.http_base}/ingest/${a.name}",
    json=payload,  # объект или список объектов (пачка)
    headers={"${e.api_key_header}": "${key}"},
    timeout=10,
)
print(r.status_code, r.json())  # 202: принято; в "rejected" — причина по каждому отклонённому`
}

/** Field rows for the "what to send" table, straight from the adapter's JSON Schema. */
export function schemaRows(a: AdapterInfo): { name: string; type: string; required: boolean; description: string }[] {
  const props = a.schema.properties ?? {}
  const required = new Set(a.schema.required ?? [])
  return Object.entries(props).map(([name, p]) => {
    const variants = p.anyOf ?? [p]
    const type = variants
      .map((v) => (v.enum ? v.enum.map((x) => JSON.stringify(x)).join(' | ') : v.const !== undefined ? JSON.stringify(v.const) : v.type))
      .filter(Boolean)
      .join(' | ')
    const range = [p.minimum != null && `≥ ${p.minimum}`, p.maximum != null && `≤ ${p.maximum}`].filter(Boolean).join(', ')
    return { name, type: type || 'any', required: required.has(name), description: [p.description, range].filter(Boolean).join('; ') }
  })
}

export const REASON_TEXT: Record<string, string> = {
  invalid_payload: 'Формат не совпадает со схемой адаптера',
  unknown_sensor: 'Датчик не зарегистрирован в реестре',
  sensor_disabled: 'Датчик выключен в реестре',
  type_mismatch: 'Тип датчика в реестре не совпадает с адаптером',
  forbidden_type: 'Ключу не разрешён этот тип датчиков',
  contract_violation: 'После нормализации событие не прошло контракт',
  clock_skew: 'Время измерения в будущем — проверьте часы устройства',
  topic_mismatch: 'Устройство в MQTT-топике не совпадает с payload',
  worker_invalid: 'Worker не смог разобрать событие',
}
