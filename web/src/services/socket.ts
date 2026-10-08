import type { Command, Telemetry } from '../types/simulation'

export type Connection = 'connecting' | 'connected' | 'offline' | 'paused'

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

  /** Stop streaming (to save bandwidth) until connect() is called again. */
  pause() {
    this.destroy()
    this.onConnection('paused')
  }

  destroy() {
    this.closed = true
    if (this.retry !== null) window.clearTimeout(this.retry)
    this.retry = null
    if (this.socket) {
      // Detach first so an intentional close is not reported as 'offline' or retried.
      this.socket.onclose = null
      this.socket.onerror = null
      this.socket.close()
      this.socket = null
    }
  }
}
