import { useCallback, useEffect, useRef, useState } from 'react'
import { TelemetrySocket, type Connection } from '../services/socket'
import type { Command, Telemetry } from '../types/simulation'

// On the hosted site telemetry costs bandwidth, so stop it for tabs nobody is watching.
// The desktop app (served from 127.0.0.1) streams locally and never pauses.
const SAVES_BANDWIDTH = !['127.0.0.1', 'localhost'].includes(window.location.hostname)
const HIDDEN_PAUSE_MS = 60_000
const IDLE_PAUSE_MS = 15 * 60_000

export function useTelemetry() {
  const [state, setState] = useState<Telemetry | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<TelemetrySocket | null>(null)
  const handleConnection = useCallback((value: Connection) => {
    setConnection(value)
    if (value === 'connected') setError(null)
  }, [])
  // The server leaves slow-changing fields (history, trails) out of most messages: keep the last copy.
  const handleState = useCallback((next: Telemetry) => setState((previous) => previous ? { ...previous, ...next } : next), [])
  useEffect(() => {
    const socket = new TelemetrySocket(handleState, handleConnection, setError)
    ref.current = socket
    socket.connect()
    if (!SAVES_BANDWIDTH) return () => socket.destroy()

    let paused = false
    let hiddenTimer: number | undefined
    let lastActivity = Date.now()
    const pause = () => { if (!paused) { paused = true; socket.pause() } }
    const resume = () => { if (paused) { paused = false; socket.connect() } }
    const onVisibility = () => {
      window.clearTimeout(hiddenTimer)
      if (document.hidden) hiddenTimer = window.setTimeout(pause, HIDDEN_PAUSE_MS)
      else { lastActivity = Date.now(); resume() }
    }
    const onActivity = () => { lastActivity = Date.now(); if (!document.hidden) resume() }
    const idleCheck = window.setInterval(() => { if (Date.now() - lastActivity > IDLE_PAUSE_MS) pause() }, 30_000)
    const activity = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const
    document.addEventListener('visibilitychange', onVisibility)
    activity.forEach((name) => window.addEventListener(name, onActivity, { passive: true }))
    return () => {
      window.clearTimeout(hiddenTimer)
      window.clearInterval(idleCheck)
      document.removeEventListener('visibilitychange', onVisibility)
      activity.forEach((name) => window.removeEventListener(name, onActivity))
      socket.destroy()
    }
  }, [handleConnection, handleState])
  const send = useCallback((command: Command) => ref.current?.send(command), [])
  return { state, connection, error, clearError: () => setError(null), reportError: setError, send }
}
