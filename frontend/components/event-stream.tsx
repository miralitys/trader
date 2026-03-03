'use client'

import { useEffect, useMemo, useState } from 'react'

import { apiBase } from '@/lib/api'

type StreamEvent = {
  type: string
  ts: string
  payload: Record<string, unknown>
}

export function EventStream() {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [toasts, setToasts] = useState<StreamEvent[]>([])

  useEffect(() => {
    const source = new EventSource(`${apiBase}/api/realtime/sse`)

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as StreamEvent
        if (parsed.type === 'heartbeat') return
        setEvents((prev) => [parsed, ...prev].slice(0, 50))
        setToasts((prev) => [...prev, parsed].slice(-4))
      } catch {
        // ignore malformed event payload
      }
    }

    return () => {
      source.close()
    }
  }, [])

  useEffect(() => {
    if (!toasts.length) return
    const timer = setTimeout(() => {
      setToasts((prev) => prev.slice(1))
    }, 3200)
    return () => clearTimeout(timer)
  }, [toasts])

  return (
    <div className="space-y-3">
      <div className="card p-3">
        <div className="flex items-center justify-between">
          <div className="font-medium">Realtime Event Feed</div>
          <div className={`text-xs font-medium ${connected ? 'text-good' : 'text-bad'}`}>
            {connected ? 'SSE connected' : 'SSE disconnected'}
          </div>
        </div>
        <div className="mt-3 max-h-64 overflow-auto space-y-2">
          {events.length === 0 ? <div className="text-sm text-muted">No events yet.</div> : null}
          {events.map((evt, idx) => (
            <div key={`${evt.ts}-${idx}`} className="rounded-lg border border-line bg-panelSoft p-2 text-xs">
              <div className="font-medium">{evt.type}</div>
              <div className="text-muted">{new Date(evt.ts).toLocaleString()}</div>
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-ink">
                {JSON.stringify(evt.payload, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      </div>

      <div className="pointer-events-none fixed bottom-4 right-4 z-50 space-y-2">
        {toasts.map((toast, idx) => (
          <div key={`${toast.ts}-toast-${idx}`} className="rounded-lg border border-line bg-panel p-3 shadow-lg">
            <div className="text-sm font-medium">{toast.type}</div>
            <div className="text-xs text-muted">{new Date(toast.ts).toLocaleTimeString()}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
