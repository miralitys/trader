'use client'

import { useEffect, useMemo, useState } from 'react'

import { CandlesChart } from '@/components/candles-chart'
import { apiFetch } from '@/lib/api'

type Universe = {
  symbols: string[]
}

type Candle = {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

type Signal = {
  symbol?: string
  entry: number
  stop: number
  take: number
  status: string
}

type Trade = {
  symbol?: string
  entry_price: number
  exit_price?: number | null
  status: string
}

export default function ChartPage() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [symbol, setSymbol] = useState('')
  const [candles, setCandles] = useState<Candle[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    ;(async () => {
      try {
        const universe = await apiFetch<Universe>('/api/universe/current')
        setSymbols(universe.symbols)
        if (universe.symbols.length) setSymbol((prev) => prev || universe.symbols[0])
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load universe')
      }
    })()
  }, [])

  async function load() {
    if (!symbol) return
    try {
      const [cs, ss, ts] = await Promise.all([
        apiFetch<Candle[]>(`/api/candles?symbol=${encodeURIComponent(symbol)}&tf=5m`),
        apiFetch<Signal[]>(`/api/signals?symbol=${encodeURIComponent(symbol)}`),
        apiFetch<Trade[]>('/api/trades?mode=paper')
      ])
      setCandles(cs)
      setSignals(ss)
      setTrades(ts.filter((t) => t.symbol === symbol))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load chart data')
    }
  }

  useEffect(() => {
    load()
  }, [symbol])

  const lines = useMemo(() => {
    const active = signals.find((s) => s.status === 'active')
    if (!active) return []
    return [
      { ts: candles[0]?.ts || '', price: active.entry, color: '#0b5bb8', label: 'Entry' },
      { ts: candles[0]?.ts || '', price: active.stop, color: '#c7363e', label: 'SL' },
      { ts: candles[0]?.ts || '', price: active.take, color: '#0f9d75', label: 'TP' }
    ]
  }, [signals, candles])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Chart</h1>
        <p className="text-sm text-muted">5m candles with signal levels and trade markers.</p>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="card p-3 flex items-end gap-2">
        <div>
          <label className="text-xs text-muted">Symbol</label>
          <select
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          >
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <button className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="card p-3">
        {candles.length ? <CandlesChart candles={candles} lines={lines} /> : <div>No candles yet.</div>}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card p-3">
          <h2 className="font-semibold">Signals ({symbol})</h2>
          <div className="mt-2 space-y-2">
            {signals.slice(0, 8).map((s, i) => (
              <div key={i} className="rounded-lg border border-line bg-panelSoft p-2 text-sm">
                <div className="font-medium">{s.status}</div>
                <div>Entry {s.entry.toFixed(4)} | SL {s.stop.toFixed(4)} | TP {s.take.toFixed(4)}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="card p-3">
          <h2 className="font-semibold">Trades ({symbol})</h2>
          <div className="mt-2 space-y-2">
            {trades.slice(0, 8).map((t, i) => (
              <div key={i} className="rounded-lg border border-line bg-panelSoft p-2 text-sm">
                <div className="font-medium">{t.status}</div>
                <div>Entry {t.entry_price.toFixed(4)}{t.exit_price ? ` | Exit ${t.exit_price.toFixed(4)}` : ''}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
