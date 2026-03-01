'use client'

import { useEffect, useMemo, useState } from 'react'

import { apiFetch } from '@/lib/api'

type Signal = {
  id: number
  symbol?: string
  strategy: string
  timeframe: string
  signal: string
  entry: number
  stop: number
  take: number
  confidence: number
  reason: string
  created_at: string
  expires_at: string
  status: string
  meta_json: Record<string, unknown>
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [selected, setSelected] = useState<Signal | null>(null)
  const [filters, setFilters] = useState({ symbol: '', strategy: '', status: '' })
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const params = new URLSearchParams()
      if (filters.symbol) params.set('symbol', filters.symbol)
      if (filters.strategy) params.set('strategy', filters.strategy)
      if (filters.status) params.set('status', filters.status)
      const data = await apiFetch<Signal[]>(`/api/signals?${params.toString()}`)
      setSignals(data)
      if (data.length && !selected) setSelected(data[0])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load signals')
    }
  }

  useEffect(() => {
    load()
  }, [filters.symbol, filters.strategy, filters.status])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Signals</h1>
        <p className="text-sm text-muted">Generated signals with status and confidence.</p>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="card p-3 flex flex-wrap gap-2 items-end">
        <div>
          <label className="text-xs text-muted">Symbol</label>
          <input
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm"
            value={filters.symbol}
            onChange={(e) => setFilters((prev) => ({ ...prev, symbol: e.target.value }))}
            placeholder="BTC-USDC"
          />
        </div>
        <div>
          <label className="text-xs text-muted">Strategy</label>
          <select
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm"
            value={filters.strategy}
            onChange={(e) => setFilters((prev) => ({ ...prev, strategy: e.target.value }))}
          >
            <option value="">All</option>
            <option value="StrategyBreakoutRetest">BreakoutRetest</option>
            <option value="StrategyPullbackToTrend">PullbackToTrend</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-muted">Status</label>
          <select
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm"
            value={filters.status}
            onChange={(e) => setFilters((prev) => ({ ...prev, status: e.target.value }))}
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="expired">Expired</option>
            <option value="executed">Executed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
        <button className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="xl:col-span-2 card p-3 overflow-auto">
          <table className="w-full text-sm min-w-[900px]">
            <thead>
              <tr className="text-left text-muted">
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Signal</th>
                <th>Entry</th>
                <th>Stop</th>
                <th>Take</th>
                <th>Conf</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr
                  key={s.id}
                  className={`border-t border-line cursor-pointer ${selected?.id === s.id ? 'bg-panelSoft' : ''}`}
                  onClick={() => setSelected(s)}
                >
                  <td className="py-2">{s.symbol}</td>
                  <td>{s.strategy}</td>
                  <td>{s.signal}</td>
                  <td>{s.entry.toFixed(4)}</td>
                  <td>{s.stop.toFixed(4)}</td>
                  <td>{s.take.toFixed(4)}</td>
                  <td>{s.confidence.toFixed(2)}</td>
                  <td>{s.status}</td>
                  <td>{new Date(s.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card p-3">
          <h2 className="font-semibold">Signal detail</h2>
          {!selected ? <div className="text-sm text-muted mt-2">Select signal from table.</div> : null}
          {selected ? (
            <div className="text-sm space-y-2 mt-2">
              <div><span className="text-muted">ID:</span> {selected.id}</div>
              <div><span className="text-muted">Symbol:</span> {selected.symbol}</div>
              <div><span className="text-muted">Strategy:</span> {selected.strategy}</div>
              <div><span className="text-muted">Timeframe:</span> {selected.timeframe}</div>
              <div><span className="text-muted">Entry / SL / TP:</span> {selected.entry.toFixed(4)} / {selected.stop.toFixed(4)} / {selected.take.toFixed(4)}</div>
              <div><span className="text-muted">Reason:</span> {selected.reason}</div>
              <div className="rounded-lg border border-line bg-panelSoft p-2">
                <div className="text-xs text-muted mb-1">Meta</div>
                <pre className="text-xs whitespace-pre-wrap font-mono">{JSON.stringify(selected.meta_json, null, 2)}</pre>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
