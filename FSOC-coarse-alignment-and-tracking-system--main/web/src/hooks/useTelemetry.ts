import { useCallback, useEffect, useRef, useState } from 'react'
import { TelemetrySocket, type Connection } from '../services/socket'
import type { Command, Telemetry } from '../types/simulation'

export function useTelemetry() {
  const [state, setState] = useState<Telemetry | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<TelemetrySocket | null>(null)
  const handleConnection = useCallback((value: Connection) => {
    setConnection(value)
    if (value === 'connected') setError(null)
  }, [])
  useEffect(() => {
    const socket = new TelemetrySocket(setState, handleConnection, setError)
    ref.current = socket
    socket.connect()
    return () => socket.destroy()
  }, [handleConnection])
  const send = useCallback((command: Command) => ref.current?.send(command), [])
  return { state, connection, error, clearError: () => setError(null), reportError: setError, send }
}
