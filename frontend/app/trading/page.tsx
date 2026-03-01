'use client'

import { useEffect, useMemo, useState } from 'react'

import { apiFetch } from '@/lib/api'

type Position = {
  id: number
  symbol?: string
  qty_base: number
  avg_price: number
  unrealized_pnl: number
  realized_pnl: number
  opened_at: string
  status: string
}

type Trade = {
  id: number
  symbol?: string
  qty_base: number
  entry_price: number
  exit_price?: number | null
  pnl: number
  fees: number
  opened_at: string
  closed_at?: string | null
  status: string
}

export default function TradingPage() {
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const [p, t] = await Promise.all([
        apiFetch<Position[]>('/api/positions?mode=paper'),
        apiFetch<Trade[]>('/api/trades?mode=paper')
      ])
      setPositions(p)
      setTrades(t)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load trading data')
    }
  }

  useEffect(() => {
    load()
  }, [])

  const openPositions = useMemo(() => positions.filter((p) => p.status === 'open'), [positions])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Trading (Paper)</h1>
        <p className="text-sm text-muted">Open positions and executed trade history in paper mode.</p>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="card p-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Open positions</h2>
          <button className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" onClick={load}>
            Refresh
          </button>
        </div>
        <div className="mt-3 overflow-auto">
          <table className="w-full text-sm min-w-[760px]">
            <thead>
              <tr className="text-left text-muted">
                <th>Symbol</th>
                <th>Qty</th>
                <th>Avg price</th>
                <th>Unrealized PnL</th>
                <th>Realized PnL</th>
                <th>Opened</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {openPositions.map((p) => (
                <tr key={p.id} className="border-t border-line">
                  <td className="py-2 font-medium">{p.symbol}</td>
                  <td>{p.qty_base.toFixed(6)}</td>
                  <td>{p.avg_price.toFixed(4)}</td>
                  <td className={p.unrealized_pnl >= 0 ? 'text-good' : 'text-bad'}>{p.unrealized_pnl.toFixed(4)}</td>
                  <td className={p.realized_pnl >= 0 ? 'text-good' : 'text-bad'}>{p.realized_pnl.toFixed(4)}</td>
                  <td>{new Date(p.opened_at).toLocaleString()}</td>
                  <td>{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!openPositions.length ? <div className="text-sm text-muted mt-3">No open positions.</div> : null}
        </div>
      </div>

      <div className="card p-3 overflow-auto">
        <h2 className="font-semibold">Trades</h2>
        <table className="w-full text-sm mt-3 min-w-[880px]">
          <thead>
            <tr className="text-left text-muted">
              <th>ID</th>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Fees</th>
              <th>PnL</th>
              <th>Opened</th>
              <th>Closed</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-t border-line">
                <td className="py-2">{t.id}</td>
                <td>{t.symbol}</td>
                <td>{t.qty_base.toFixed(6)}</td>
                <td>{t.entry_price.toFixed(4)}</td>
                <td>{t.exit_price ? t.exit_price.toFixed(4) : '-'}</td>
                <td>{t.fees.toFixed(4)}</td>
                <td className={t.pnl >= 0 ? 'text-good' : 'text-bad'}>{t.pnl.toFixed(4)}</td>
                <td>{new Date(t.opened_at).toLocaleString()}</td>
                <td>{t.closed_at ? new Date(t.closed_at).toLocaleString() : '-'}</td>
                <td>{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
