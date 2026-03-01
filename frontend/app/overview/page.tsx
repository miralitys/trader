'use client'

import { useEffect, useMemo, useState } from 'react'

import { EventStream } from '@/components/event-stream'
import { apiFetch } from '@/lib/api'

type Health = {
  status: string
  paper_enabled: boolean
  live_enabled: boolean
  kill_switch_paused: boolean
  server_time: string
  last_data_sync_at?: string | null
  data_delay_seconds?: number | null
}

type Universe = {
  symbols: string[]
  ranked: Array<{ symbol: string; quote_volume_30d: number }>
  source: string
  updated_at?: string | null
}

type Trade = {
  pnl: number
  opened_at: string
  closed_at?: string | null
  status: string
}

export default function OverviewPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [universe, setUniverse] = useState<Universe | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const [h, u, t] = await Promise.all([
        apiFetch<Health>('/api/health'),
        apiFetch<Universe>('/api/universe/current'),
        apiFetch<Trade[]>('/api/trades?mode=paper')
      ])
      setHealth(h)
      setUniverse(u)
      setTrades(t)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load overview')
    }
  }

  useEffect(() => {
    load()
  }, [])

  const metrics = useMemo(() => {
    const now = Date.now()
    const pnl7 = trades
      .filter((t) => t.closed_at && now - new Date(t.closed_at).getTime() <= 7 * 86400_000)
      .reduce((acc, t) => acc + (t.pnl || 0), 0)
    const pnl30 = trades
      .filter((t) => t.closed_at && now - new Date(t.closed_at).getTime() <= 30 * 86400_000)
      .reduce((acc, t) => acc + (t.pnl || 0), 0)
    return { pnl7, pnl30 }
  }, [trades])

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Overview</h1>
          <p className="text-sm text-muted">System state, universe, latency and paper PnL snapshot.</p>
        </div>
        <button className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" onClick={load}>
          Refresh
        </button>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="card p-4">
          <div className="text-xs uppercase text-muted">Paper mode</div>
          <div className={`text-lg font-semibold ${health?.paper_enabled ? 'text-good' : 'text-bad'}`}>
            {health?.paper_enabled ? 'ON' : 'OFF'}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted">Live mode</div>
          <div className={`text-lg font-semibold ${health?.live_enabled ? 'text-warn' : 'text-muted'}`}>
            {health?.live_enabled ? 'ON' : 'OFF'}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted">Kill switch</div>
          <div className={`text-lg font-semibold ${health?.kill_switch_paused ? 'text-bad' : 'text-good'}`}>
            {health?.kill_switch_paused ? 'PAUSED' : 'ACTIVE'}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted">Data delay</div>
          <div className="text-lg font-semibold">{health?.data_delay_seconds ?? '-'}s</div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card p-4">
          <h2 className="font-semibold">Universe TOP-5</h2>
          <div className="text-xs text-muted mt-1">Selection basis: volume rank (30d quote volume)</div>
          <div className="mt-3 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted">
                  <th>Symbol</th>
                  <th>30d $ volume</th>
                </tr>
              </thead>
              <tbody>
                {(universe?.ranked || []).map((row) => (
                  <tr key={row.symbol} className="border-t border-line">
                    <td className="py-2 font-medium">{row.symbol}</td>
                    <td className="py-2">{Number(row.quote_volume_30d || 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 text-xs text-muted">
            Last recompute: {universe?.updated_at ? new Date(universe.updated_at).toLocaleString() : '-'}
          </div>
        </div>

        <div className="card p-4">
          <h2 className="font-semibold">Paper metrics</h2>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <div className="rounded-lg border border-line bg-panelSoft p-3">
              <div className="text-xs text-muted">PnL 7d</div>
              <div className={`text-xl font-semibold ${metrics.pnl7 >= 0 ? 'text-good' : 'text-bad'}`}>
                {metrics.pnl7.toFixed(2)}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-panelSoft p-3">
              <div className="text-xs text-muted">PnL 30d</div>
              <div className={`text-xl font-semibold ${metrics.pnl30 >= 0 ? 'text-good' : 'text-bad'}`}>
                {metrics.pnl30.toFixed(2)}
              </div>
            </div>
          </div>
          <div className="mt-3 text-xs text-muted">
            Last sync: {health?.last_data_sync_at ? new Date(health.last_data_sync_at).toLocaleString() : '-'}
          </div>
        </div>
      </div>

      <EventStream />
    </div>
  )
}
