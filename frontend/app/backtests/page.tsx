'use client'

import { useEffect, useMemo, useState } from 'react'
import { createChart, type UTCTimestamp } from 'lightweight-charts'

import { apiFetch } from '@/lib/api'

type StrategyKey =
  | 'StrategyBreakoutRetest'
  | 'StrategyPullbackToTrend'
  | 'MeanReversionHardStop'
  | 'StrategyTrendRetrace70'

type Backtest = {
  id: number
  strategy: string
  status: string
  start_ts: string
  end_ts: string
  created_at: string
  params_json: Record<string, unknown>
  metrics_json: Record<string, unknown>
  equity_curve_json: Array<{ ts: string; equity: number }>
}

type BacktestHistoryReadiness = {
  ready: boolean
  reason: string
  strategy_requested: string
  strategy_runtime: string
  period_requested: { start_ts: string; end_ts: string }
  period_effective: { start_ts: string; end_ts: string }
  coverage: {
    effective_ratio: number
    required_ratio: number
    min_ratio: number
    target_ratio: number
  }
  universe: {
    input_tickers: string[]
    selected_top5: string[]
    selection_source: string
  }
}

type StrategyDefinition = {
  key: StrategyKey
  label: string
}

const STRATEGIES: StrategyDefinition[] = [
  { key: 'StrategyBreakoutRetest', label: 'BreakoutRetest' },
  { key: 'StrategyPullbackToTrend', label: 'PullbackToTrend' },
  { key: 'MeanReversionHardStop', label: 'MeanReversionHardStop' },
  { key: 'StrategyTrendRetrace70', label: 'TrendRetrace70' }
]

const ACTIVE_STATUSES = new Set(['queued', 'running', 'cancelling'])
const POLL_INTERVAL_MS = 15_000

function EquityMiniChart({ points }: { points: Array<{ ts: string; equity: number }> }) {
  const chartId = useMemo(() => `equity-${Math.random().toString(36).slice(2)}`, [])

  useEffect(() => {
    const element = document.getElementById(chartId)
    if (!element || points.length === 0) return

    const chart = createChart(element, {
      width: element.clientWidth,
      height: 220,
      layout: { textColor: '#0f1720', background: { color: '#fff' } },
      grid: { vertLines: { color: '#edf1f4' }, horzLines: { color: '#edf1f4' } }
    })

    const line = chart.addLineSeries({ color: '#0b5bb8', lineWidth: 2 })
    line.setData(
      points.map((point) => ({
        time: Math.floor(new Date(point.ts).getTime() / 1000) as UTCTimestamp,
        value: point.equity
      }))
    )
    chart.timeScale().fitContent()

    return () => chart.remove()
  }, [chartId, points])

  return <div id={chartId} className="w-full" />
}

function getBaseMetrics(row: Backtest | null): Record<string, unknown> {
  if (!row) return {}
  const nested = row.metrics_json?.base
  if (nested && typeof nested === 'object') return nested as Record<string, unknown>
  return row.metrics_json ?? {}
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function metricText(value: unknown, format?: (num: number) => string): string {
  const num = toNumber(value)
  if (num === null) return '-'
  return format ? format(num) : String(num)
}

function statusClass(status: string | null): string {
  if (status === 'completed') return 'text-good'
  if (status === 'running' || status === 'queued' || status === 'cancelling') return 'text-warn'
  if (status === 'failed' || status === 'cancelled') return 'text-bad'
  return 'text-muted'
}

export default function BacktestsPage() {
  const [rows, setRows] = useState<Backtest[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyKey>('StrategyBreakoutRetest')
  const [runningByStrategy, setRunningByStrategy] = useState<Record<string, boolean>>({})
  const [stoppingByStrategy, setStoppingByStrategy] = useState<Record<string, boolean>>({})
  const [clearingByStrategy, setClearingByStrategy] = useState<Record<string, boolean>>({})
  const [readinessByStrategy, setReadinessByStrategy] = useState<Record<string, BacktestHistoryReadiness | null>>({})
  const [readinessLoadingByStrategy, setReadinessLoadingByStrategy] = useState<Record<string, boolean>>({})
  const [refreshing, setRefreshing] = useState(false)

  async function loadBacktests(force = false) {
    try {
      const path = force ? `/api/backtests?refresh_ts=${Date.now()}` : '/api/backtests'
      const data = await apiFetch<Backtest[]>(path)
      setRows(data)
      setLastUpdatedAt(Date.now())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load backtests')
    }
  }

  async function runStrategy(strategy: StrategyKey) {
    setRunningByStrategy((prev) => ({ ...prev, [strategy]: true }))
    setError(null)
    try {
      await apiFetch('/api/backtests/run', {
        method: 'POST',
        body: JSON.stringify({ strategy })
      })
      await loadBacktests(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to run ${strategy}`)
    } finally {
      setRunningByStrategy((prev) => ({ ...prev, [strategy]: false }))
    }
  }

  async function stopStrategy(strategy: StrategyKey, backtestId: number) {
    setStoppingByStrategy((prev) => ({ ...prev, [strategy]: true }))
    setError(null)
    try {
      await apiFetch(`/api/backtests/${backtestId}/cancel`, { method: 'POST' })
      await loadBacktests(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to stop ${strategy}`)
    } finally {
      setStoppingByStrategy((prev) => ({ ...prev, [strategy]: false }))
    }
  }

  async function clearStrategyHistory(strategy: StrategyKey) {
    setClearingByStrategy((prev) => ({ ...prev, [strategy]: true }))
    setError(null)
    try {
      await apiFetch(`/api/backtests/strategy/${encodeURIComponent(strategy)}`, { method: 'DELETE' })
      await loadBacktests(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to clear ${strategy} history`)
    } finally {
      setClearingByStrategy((prev) => ({ ...prev, [strategy]: false }))
    }
  }

  async function refreshNow() {
    setRefreshing(true)
    try {
      await Promise.all([loadBacktests(true), loadReadinessForAll()])
    } finally {
      setRefreshing(false)
    }
  }

  async function loadReadiness(strategy: StrategyKey) {
    setReadinessLoadingByStrategy((prev) => ({ ...prev, [strategy]: true }))
    try {
      const data = await apiFetch<BacktestHistoryReadiness>('/api/backtests/history-readiness', {
        method: 'POST',
        body: JSON.stringify({ strategy })
      })
      setReadinessByStrategy((prev) => ({ ...prev, [strategy]: data }))
    } catch {
      setReadinessByStrategy((prev) => ({ ...prev, [strategy]: null }))
    } finally {
      setReadinessLoadingByStrategy((prev) => ({ ...prev, [strategy]: false }))
    }
  }

  async function loadReadinessForAll() {
    await Promise.all(STRATEGIES.map((strategy) => loadReadiness(strategy.key)))
  }

  useEffect(() => {
    void Promise.all([loadBacktests(), loadReadinessForAll()])
  }, [])

  const rowsByStrategy = useMemo(() => {
    return STRATEGIES.reduce<Record<StrategyKey, Backtest[]>>((acc, strategy) => {
      acc[strategy.key] = rows.filter((row) => row.strategy === strategy.key)
      return acc
    }, {} as Record<StrategyKey, Backtest[]>)
  }, [rows])

  const hasActiveRuns = useMemo(() => {
    return rows.some((row) => ACTIVE_STATUSES.has(row.status))
  }, [rows])

  const activeStrategyDefinition = useMemo(() => {
    return STRATEGIES.find((strategy) => strategy.key === selectedStrategy) ?? STRATEGIES[0]
  }, [selectedStrategy])

  useEffect(() => {
    if (!hasActiveRuns) return
    const timer = window.setInterval(() => {
      void loadBacktests()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [hasActiveRuns])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Backtests</h1>
          <p className="text-sm text-muted">
            4 independent strategies. Each run uses the backend rolling 2-year window and ignores legacy Risk/Strategy/Fees settings.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-lg border border-line bg-panel px-3 py-2 text-sm disabled:opacity-50"
            onClick={() => void refreshNow()}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
          <div className="text-xs text-muted">
            {lastUpdatedAt ? `Updated ${new Date(lastUpdatedAt).toLocaleTimeString()}` : 'Not updated yet'}
          </div>
        </div>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}

      <div className="card p-2 sm:p-3">
        <div className="flex flex-wrap gap-2">
          {STRATEGIES.map((strategy) => {
            const isActive = strategy.key === activeStrategyDefinition.key
            return (
              <button
                key={strategy.key}
                type="button"
                className={`rounded-lg px-3 py-2 text-sm transition ${
                  isActive ? 'bg-accent text-white' : 'border border-line bg-panel text-foreground'
                }`}
                onClick={() => setSelectedStrategy(strategy.key)}
              >
                {strategy.label}
              </button>
            )
          })}
        </div>
      </div>

      {(() => {
        const strategy = activeStrategyDefinition
        const strategyRows = rowsByStrategy[strategy.key] || []
        const latestRow = strategyRows[0] || null
        const activeRow = strategyRows.find((row) => ACTIVE_STATUSES.has(row.status)) || null
        const baseMetrics = getBaseMetrics(latestRow)
        const readiness = readinessByStrategy[strategy.key] || null
        const runBlockedByHistory = readiness !== null && !readiness.ready
        const lastError =
          typeof latestRow?.metrics_json?.error === 'string' ? String(latestRow.metrics_json.error) : null

        return (
          <section className="card p-4 space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{strategy.label}</h2>
                <div className="text-xs text-muted">
                  History: {strategyRows.length} run(s)
                  {latestRow ? ` | Last run ${new Date(latestRow.created_at).toLocaleString()}` : ''}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded-lg bg-accent text-white px-3 py-2 text-sm disabled:opacity-50"
                  onClick={() => void runStrategy(strategy.key)}
                  disabled={Boolean(
                    runningByStrategy[strategy.key] ||
                      clearingByStrategy[strategy.key] ||
                      readinessLoadingByStrategy[strategy.key] ||
                      runBlockedByHistory
                  )}
                >
                  {runningByStrategy[strategy.key]
                    ? 'Starting...'
                    : readinessLoadingByStrategy[strategy.key]
                      ? 'Checking history...'
                      : runBlockedByHistory
                        ? 'Run blocked'
                        : 'Run strategy'}
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-line bg-panel px-3 py-2 text-sm disabled:opacity-50"
                  onClick={() => activeRow && void stopStrategy(strategy.key, activeRow.id)}
                  disabled={!activeRow || Boolean(stoppingByStrategy[strategy.key] || clearingByStrategy[strategy.key])}
                >
                  {stoppingByStrategy[strategy.key] ? 'Stopping...' : 'Stop strategy'}
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-line bg-panel px-3 py-2 text-sm disabled:opacity-50"
                  onClick={() => void clearStrategyHistory(strategy.key)}
                  disabled={Boolean(clearingByStrategy[strategy.key])}
                >
                  {clearingByStrategy[strategy.key] ? 'Clearing...' : 'Clear history'}
                </button>
              </div>
            </div>

            {runBlockedByHistory ? (
              <div className="rounded-lg border border-line bg-panelSoft p-3 text-sm text-bad">
                History not ready: {readiness?.reason || 'unknown'}
                {readiness?.coverage ? (
                  <div className="text-xs mt-1">
                    Coverage {(readiness.coverage.effective_ratio * 100).toFixed(1)}% / required{' '}
                    {(readiness.coverage.required_ratio * 100).toFixed(1)}%
                  </div>
                ) : null}
              </div>
            ) : null}

            {lastError ? (
              <div className="rounded-lg border border-line bg-panelSoft p-3 text-sm text-bad whitespace-pre-wrap">
                {lastError}
              </div>
            ) : null}

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Status</div>
                <div className={`text-lg font-semibold ${statusClass(latestRow?.status || null)}`}>
                  {latestRow?.status || 'no history'}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Trades</div>
                <div className="text-lg font-semibold">{metricText(baseMetrics.trades)}</div>
              </div>
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">PF</div>
                <div className="text-lg font-semibold">{metricText(baseMetrics.profit_factor, (num) => num.toFixed(2))}</div>
              </div>
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Winrate</div>
                <div className="text-lg font-semibold">{metricText(baseMetrics.winrate, (num) => `${(num * 100).toFixed(1)}%`)}</div>
              </div>
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Max DD</div>
                <div className="text-lg font-semibold">
                  {metricText(baseMetrics.max_drawdown_pct, (num) => `${num.toFixed(2)}%`)}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[1.2fr_0.8fr] gap-4">
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs text-muted mb-2">Equity curve</div>
                {latestRow?.equity_curve_json?.length ? (
                  <EquityMiniChart points={latestRow.equity_curve_json} />
                ) : (
                  <div className="text-sm text-muted">No equity data yet.</div>
                )}
              </div>

              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs text-muted mb-2">Assumptions / Stress metrics</div>
                <pre className="text-xs whitespace-pre-wrap font-mono">
                  {JSON.stringify(
                    latestRow
                      ? {
                          assumptions: latestRow.metrics_json?.assumptions ?? null,
                          base: latestRow.metrics_json?.base ?? latestRow.metrics_json ?? {},
                          stress_1_5x: latestRow.metrics_json?.stress_1_5x ?? null,
                          stress_2_0x: latestRow.metrics_json?.stress_2_0x ?? null
                        }
                      : null,
                    null,
                    2
                  )}
                </pre>
              </div>
            </div>
          </section>
        )
      })()}
    </div>
  )
}
