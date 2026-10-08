import type { Command, Telemetry } from '../types/simulation'

export type Connection = 'connecting' | 'connected' | 'offline'

export class TelemetrySocket {
  private socket: WebSocket | null = null
  private retry: number | null = null
  private closed = false
  constructor(private onState: (state: Telemetry) => void, private onConnection: (value: Connection) => void, private onError: (message: string) => void) {}

  connect() {
    this.closed = false
    this.onConnection('connecting')
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = (import.meta.env.VITE_WS_URL as string | undefined) || `${protocol}://${window.location.host}/ws`
    this.socket = new WebSocket(url)
    this.socket.onopen = () => this.onConnection('connected')
    this.socket.onmessage = (event) => {
      try {
        const body = JSON.parse(event.data) as Telemetry | { type: 'error'; message: string }
        if (body.type === 'error') this.onError(body.message)
        else this.onState(body)
      } catch { this.onError('Received invalid telemetry from the backend.') }
    }
    this.socket.onerror = () => this.onError('WebSocket connection failed.')
    this.socket.onclose = () => {
      this.onConnection('offline')
      if (!this.closed) this.retry = window.setTimeout(() => this.connect(), 1600)
    }
  }

  send(command: Command) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(command))
    else this.onError('Backend is disconnected. The requested command was not sent.')
  }

  destroy() {
    this.closed = true
    if (this.retry !== null) window.clearTimeout(this.retry)
    this.socket?.close()
  }
}
