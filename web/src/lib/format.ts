const time = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const dateTime = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export const fmtTime = (iso: string | null | undefined) => (iso ? time.format(new Date(iso)) : '—')
export const fmtDateTime = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : '—')

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s} с назад`
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`
  return `${Math.floor(s / 86400)} д назад`
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)} с`
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`
  return `${(seconds / 3600).toFixed(1)} ч`
}

export function fmtValue(v: unknown, unit?: string | null): string {
  if (typeof v === 'boolean') return v ? 'да' : 'нет'
  if (typeof v === 'number') {
    const n = Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)
    return unit ? `${n} ${unit}` : n
  }
  return v == null ? '—' : String(v)
}

export const SEVERITY_LABEL = { info: 'Инфо', warning: 'Предупреждение', critical: 'Критично' } as const
export const STATUS_LABEL = { open: 'Открыта', ack: 'Подтверждена', resolved: 'Закрыта' } as const
export const SENSOR_STATUS_LABEL = { ok: 'Норма', warning: 'Вне нормы', critical: 'Критично', offline: 'Нет связи' } as const
export const VEHICLE_STATUS_LABEL = { moving: 'Едет', idle: 'Стоит, двигатель работает', stopped: 'Стоит', offline: 'Нет связи' } as const
export const VEHICLE_KIND_LABEL = { truck: 'Грузовик', loader: 'Погрузчик', car: 'Легковой' } as const
export const ALERT_KIND_LABEL = {
  threshold: 'Пороги',
  geozone: 'Геозона',
  schedule: 'Без СКУД / вне графика',
  whitelist: 'Список допуска',
  speed: 'Скорость',
  offline: 'Нет связи',
  breakdown: 'Простой техники',
} as const

/** ISO string for <input type="datetime-local"> in local time and back. */
export function toLocalInput(d: Date): string {
  const off = d.getTimezoneOffset() * 60000
  return new Date(d.getTime() - off).toISOString().slice(0, 16)
}
export const fromLocalInput = (s: string) => new Date(s).toISOString()
