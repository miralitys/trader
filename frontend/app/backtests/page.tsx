'use client'

import { useEffect, useMemo, useState } from 'react'
import { createChart, UTCTimestamp } from 'lightweight-charts'

import { apiBase, apiFetch } from '@/lib/api'

type Backtest = {
  id: number
  strategy: string
  status: string
  start_ts: string
  end_ts: string
  created_at: string
  metrics_json: Record<string, unknown>
  equity_curve_json: Array<{ ts: string; equity: number }>
}

function EquityMiniChart({ points }: { points: Array<{ ts: string; equity: number }> }) {
  const id = useMemo(() => `equity-${Math.random().toString(36).slice(2)}`, [])

  useEffect(() => {
    const element = document.getElementById(id)
    if (!element || points.length === 0) return
    const chart = createChart(element, {
      width: element.clientWidth,
      height: 220,
      layout: { textColor: '#0f1720', background: { color: '#fff' } },
      grid: { vertLines: { color: '#edf1f4' }, horzLines: { color: '#edf1f4' } }
    })
    const line = chart.addLineSeries({ color: '#0b5bb8', lineWidth: 2 })
    line.setData(
      points.map((p) => ({
        time: Math.floor(new Date(p.ts).getTime() / 1000) as UTCTimestamp,
        value: p.equity
      }))
    )
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [id, points])

  return <div id={id} className="w-full" />
}

export default function BacktestsPage() {
  const defaultEnd = new Date()
  const defaultStart = new Date(defaultEnd)
  defaultStart.setFullYear(defaultStart.getFullYear() - 2)

  const [rows, setRows] = useState<Backtest[]>([])
  const [selected, setSelected] = useState<Backtest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [strategy, setStrategy] = useState('StrategyBreakoutRetest')
  const [startTs, setStartTs] = useState(defaultStart.toISOString())
  const [endTs, setEndTs] = useState(defaultEnd.toISOString())

  function baseMetrics(row: Backtest): Record<string, number> {
    const nested = row.metrics_json?.base as Record<string, number> | undefined
    if (nested && typeof nested === 'object') return nested
    return row.metrics_json as Record<string, number>
  }

  async function load() {
    try {
      const data = await apiFetch<Backtest[]>('/api/backtests')
      setRows(data)
      if (!selected && data.length) setSelected(data[0])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load backtests')
    }
  }

  async function runBacktest() {
    setRunning(true)
    setError(null)
    try {
      await apiFetch('/api/backtests/run', {
        method: 'POST',
        body: JSON.stringify({
          strategy,
          start_ts: startTs || undefined,
          end_ts: endTs || undefined,
          params: {}
        })
      })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run backtest')
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Backtests</h1>
        <p className="text-sm text-muted">Run async backtests and inspect equity/metrics.</p>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="card p-3 grid grid-cols-1 md:grid-cols-4 gap-2">
        <div>
          <label className="text-xs text-muted">Strategy</label>
          <select
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            <option value="StrategyBreakoutRetest">BreakoutRetest</option>
            <option value="StrategyPullbackToTrend">PullbackToTrend</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-muted">Start</label>
          <input
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
            value={startTs}
            onChange={(e) => setStartTs(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-muted">End</label>
          <input
            className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
            value={endTs}
            onChange={(e) => setEndTs(e.target.value)}
          />
        </div>
        <div className="flex items-end gap-2">
          <button
            className="rounded-lg bg-accent text-white px-3 py-2 text-sm disabled:opacity-50"
            onClick={runBacktest}
            disabled={running}
          >
            {running ? 'Running...' : 'Run'}
          </button>
          <button className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" onClick={load}>
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="xl:col-span-2 card p-3 overflow-auto">
          <table className="w-full text-sm min-w-[760px]">
            <thead>
              <tr className="text-left text-muted">
                <th>ID</th>
                <th>Strategy</th>
                <th>Status</th>
                <th>Trades</th>
                <th>PF</th>
                <th>Winrate</th>
                <th>Max DD</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  className={`border-t border-line cursor-pointer ${selected?.id === r.id ? 'bg-panelSoft' : ''}`}
                  onClick={() => setSelected(r)}
                >
                  <td className="py-2">{r.id}</td>
                  <td>{r.strategy}</td>
                  <td>{r.status}</td>
                  <td>{baseMetrics(r)?.trades ?? '-'}</td>
                  <td>{Number(baseMetrics(r)?.profit_factor ?? 0).toFixed(2)}</td>
                  <td>{Number((baseMetrics(r)?.winrate ?? 0) * 100).toFixed(1)}%</td>
                  <td>{Number(baseMetrics(r)?.max_drawdown_pct ?? 0).toFixed(2)}%</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card p-3">
          <h2 className="font-semibold">Run detail</h2>
          {!selected ? <div className="text-sm text-muted mt-2">Select run from table.</div> : null}
          {selected ? (
            <div className="space-y-3 mt-2">
              <div className="text-sm">
                <div><span className="text-muted">ID:</span> {selected.id}</div>
                <div><span className="text-muted">Strategy:</span> {selected.strategy}</div>
                <div><span className="text-muted">Status:</span> {selected.status}</div>
                <div><span className="text-muted">Period:</span> {new Date(selected.start_ts).toLocaleDateString()} - {new Date(selected.end_ts).toLocaleDateString()}</div>
              </div>

              <div className="text-xs text-muted">Equity curve</div>
              <div className="rounded-lg border border-line bg-panelSoft p-2">
                {selected.equity_curve_json?.length ? (
                  <EquityMiniChart points={selected.equity_curve_json} />
                ) : (
                  <div className="text-sm text-muted">No equity points yet.</div>
                )}
              </div>

              <div className="flex gap-2">
                <a
                  className="rounded-lg border border-line bg-panel px-3 py-1 text-sm"
                  href={`${apiBase}/api/backtests/${selected.id}/export?fmt=json`}
                  target="_blank"
                >
                  Export JSON
                </a>
                <a
                  className="rounded-lg border border-line bg-panel px-3 py-1 text-sm"
                  href={`${apiBase}/api/backtests/${selected.id}/export?fmt=csv`}
                  target="_blank"
                >
                  Export CSV
                </a>
              </div>

              <div className="rounded-lg border border-line bg-panelSoft p-2">
                <div className="text-xs text-muted mb-1">Assumptions / Stress metrics</div>
                <pre className="text-xs whitespace-pre-wrap font-mono">
                  {JSON.stringify(
                    {
                      assumptions: selected.metrics_json?.assumptions,
                      base: selected.metrics_json?.base || selected.metrics_json,
                      stress_1_5x: selected.metrics_json?.stress_1_5x,
                      stress_2_0x: selected.metrics_json?.stress_2_0x,
                      data_availability: selected.metrics_json?.data_availability
                    },
                    null,
                    2
                  )}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
