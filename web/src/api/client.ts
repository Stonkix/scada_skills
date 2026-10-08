import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './schema'
import type { TokenPair } from './types'

export const API_BASE = '/api'
const STORAGE_KEY = 'scada.auth'

/** Token storage shared by the HTTP client, the WebSocket and the auth store. */
export const tokens = {
  get(): TokenPair | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      return raw ? (JSON.parse(raw) as TokenPair) : null
    } catch {
      return null
    }
  },
  set(pair: TokenPair | null) {
    try {
      if (pair) localStorage.setItem(STORAGE_KEY, JSON.stringify(pair))
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* private mode: stay logged in for this tab only */
    }
    listeners.forEach((fn) => fn(pair))
  },
}

const listeners = new Set<(pair: TokenPair | null) => void>()
export function onTokens(fn: (pair: TokenPair | null) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

let refreshing: Promise<boolean> | null = null

/** One refresh at a time: parallel 401s wait for the same request. */
export function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const current = tokens.get()
    if (!current) return false
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      })
      if (!res.ok) {
        tokens.set(null)
        return false
      }
      tokens.set((await res.json()) as TokenPair)
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

const auth: Middleware = {
  async onRequest({ request }) {
    const t = tokens.get()
    if (t) request.headers.set('Authorization', `Bearer ${t.access_token}`)
    return request
  },
  async onResponse({ request, response }) {
    if (response.status !== 401 || request.url.includes('/auth/')) return response
    if (!(await refreshTokens())) return response
    const retry = request.clone()
    retry.headers.set('Authorization', `Bearer ${tokens.get()!.access_token}`)
    return fetch(retry)
  },
}

export const api = createClient<paths>({ baseUrl: API_BASE })
api.use(auth)

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
}

/** Unwrap an openapi-fetch result: data or a thrown ApiError with the server's `detail`. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.data !== undefined && r.response.ok) return r.data
  const detail = (r.error as { detail?: unknown } | undefined)?.detail ?? r.error ?? r.response.statusText
  throw new ApiError(r.response.status, detail)
}

/** Human-readable error text for toasts. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (Array.isArray(e.detail)) {
      return e.detail.map((d) => (typeof d === 'string' ? d : (d as { msg?: string }).msg ?? JSON.stringify(d))).join('; ')
    }
    return typeof e.detail === 'string' ? e.detail : e.message
  }
  return e instanceof Error ? e.message : String(e)
}

export interface IngestReply {
  accepted: number
  event_ids: string[]
  rejected: { index: number; reason: string; detail: unknown }[]
}

/** Send to the connectors service exactly as a device would (proxied as /conn). */
export async function ingest(adapter: string, apiKey: string, body: unknown): Promise<{ status: number; body: Partial<IngestReply> & { detail?: unknown } }> {
  const res = await fetch(`/conn/ingest/${adapter}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
    body: JSON.stringify(body),
  })
  return { status: res.status, body: await res.json().catch(() => ({ detail: res.statusText })) }
}

/** Adapter catalogue of the connectors service. */
export async function adapters<T>(): Promise<T> {
  const res = await fetch('/conn/adapters')
  if (!res.ok) throw new ApiError(res.status, 'Сервис коннекторов недоступен')
  return (await res.json()) as T
}

/** Simulator control (demo "cheat menu"); proxied as /sim. */
export async function sim<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/sim${path}`, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, (body as { detail?: unknown }).detail ?? res.statusText)
  return body as T
}
