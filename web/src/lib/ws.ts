import { API_BASE, refreshTokens, tokens } from '@/api/client'
import type { WsMessage, WsSubscribe } from '@/api/types'

const UNAUTHORIZED = 4401

export type WsStatus = 'connecting' | 'open' | 'closed'

/**
 * WS /ws/live with automatic reconnect (exponential backoff up to 15 s) and
 * re-subscription. A 4401 close means the access token expired: refresh it first.
 */
export class LiveSocket {
  private ws: WebSocket | null = null
  private retry = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private stopped = false
  private sub: WsSubscribe = { op: 'subscribe' }

  constructor(
    private onMessage: (msg: WsMessage) => void,
    private onStatus: (s: WsStatus) => void,
  ) {}

  start() {
    this.stopped = false
    this.connect()
  }

  stop() {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
  }

  subscribe(sub: Omit<WsSubscribe, 'op'>) {
    this.sub = { op: 'subscribe', ...sub }
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(this.sub))
  }

  private url(): string {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}${API_BASE}/ws/live?token=${encodeURIComponent(tokens.get()?.access_token ?? '')}`
  }

  private connect() {
    if (this.stopped || !tokens.get()) return
    this.onStatus('connecting')
    const ws = new WebSocket(this.url())
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.onStatus('open')
      ws.send(JSON.stringify(this.sub))
    }
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type !== 'error') this.onMessage(msg as WsMessage)
      } catch {
        /* ignore malformed frames */
      }
    }
    ws.onclose = async (e) => {
      if (this.ws !== ws) return
      this.onStatus('closed')
      if (this.stopped) return
      if (e.code === UNAUTHORIZED && !(await refreshTokens())) return // logged out
      const delay = e.code === UNAUTHORIZED ? 0 : Math.min(15000, 500 * 2 ** this.retry++)
      this.timer = setTimeout(() => this.connect(), delay)
    }
  }
}
