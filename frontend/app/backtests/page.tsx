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

type BacktestProgress = {
  generated_at: string
  summary: {
    ready_strategies: number
    total_strategies: number
    not_ready_strategies: number
    all_ready: boolean
  }
  backfill_status: {
    state: string
    updated_at: string | null
    details: Record<string, unknown>
  }
  timeframes: Array<{
    timeframe: string
    candles: number
    instruments: number
    oldest_ts: string | null
    latest_ts: string | null
  }>
  strategies: Array<{
    strategy: StrategyKey
    ready: boolean
    reason: string
    effective_ratio: number
    required_ratio: number
    selected_top5: string[]
  }>
}

type ProgressSnapshot = {
  ts: string
  timeframes: Array<{
    timeframe: string
    candles: number
    latest_ts: string | null
  }>
  strategies: Array<{
    strategy: StrategyKey
    effective_ratio: number
    required_ratio: number
  }>
}

function isStrategyKey(value: unknown): value is StrategyKey {
  return STRATEGIES.some((strategy) => strategy.key === value)
}

function sanitizeProgressSnapshot(value: unknown): ProgressSnapshot | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (typeof raw.ts !== 'string') return null

  const timeframes = Array.isArray(raw.timeframes)
    ? raw.timeframes
        .map((item) => {
          if (!item || typeof item !== 'object') return null
          const entry = item as Record<string, unknown>
          if (typeof entry.timeframe !== 'string') return null
          return {
            timeframe: entry.timeframe,
            candles: typeof entry.candles === 'number' && Number.isFinite(entry.candles) ? entry.candles : 0,
            latest_ts: typeof entry.latest_ts === 'string' ? entry.latest_ts : null
          }
        })
        .filter(Boolean) as ProgressSnapshot['timeframes']
    : []

  const strategies = Array.isArray(raw.strategies)
    ? raw.strategies
        .map((item) => {
          if (!item || typeof item !== 'object') return null
          const entry = item as Record<string, unknown>
          if (!isStrategyKey(entry.strategy)) return null
          return {
            strategy: entry.strategy,
            effective_ratio:
              typeof entry.effective_ratio === 'number' && Number.isFinite(entry.effective_ratio)
                ? entry.effective_ratio
                : 0,
            required_ratio:
              typeof entry.required_ratio === 'number' && Number.isFinite(entry.required_ratio)
                ? entry.required_ratio
                : 0
          }
        })
        .filter(Boolean) as ProgressSnapshot['strategies']
    : []

  return {
    ts: raw.ts,
    timeframes,
    strategies
  }
}

const STRATEGIES: StrategyDefinition[] = [
  { key: 'StrategyBreakoutRetest', label: 'BreakoutRetest' },
  { key: 'StrategyPullbackToTrend', label: 'PullbackToTrend' },
  { key: 'MeanReversionHardStop', label: 'MeanReversionHardStop' },
  { key: 'StrategyTrendRetrace70', label: 'TrendRetrace70' }
]

const ACTIVE_STATUSES = new Set(['queued', 'running', 'cancelling'])
const POLL_INTERVAL_MS = 600_000
const PROGRESS_HISTORY_KEY = 'backtest_progress_history_v1'
const MAX_PROGRESS_SNAPSHOTS = 48

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

function diagnosticsValue(metrics: Record<string, unknown> | null, path: string[]): unknown {
  let current: unknown = metrics
  for (const key of path) {
    if (!current || typeof current !== 'object' || !(key in current)) return null
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

function statusClass(status: string | null): string {
  if (status === 'completed') return 'text-good'
  if (status === 'running' || status === 'queued' || status === 'cancelling') return 'text-warn'
  if (status === 'failed' || status === 'cancelled') return 'text-bad'
  return 'text-muted'
}

function formatEtaMinutes(minutes: number | null): string {
  if (minutes === null || !Number.isFinite(minutes) || minutes <= 0) return '-'
  if (minutes < 60) return `~${Math.ceil(minutes)} min`
  const hours = minutes / 60
  if (hours < 48) return `~${hours.toFixed(1)} h`
  return `~${(hours / 24).toFixed(1)} d`
}

function progressTone(deltaPerHour: number, candleDelta: number): string {
  if (candleDelta > 0 || deltaPerHour > 0.01) return 'text-good'
  if (deltaPerHour > 0.001) return 'text-warn'
  return 'text-bad'
}

function progressLabel(deltaPerHour: number, candleDelta: number): string {
  if (candleDelta > 0) return 'backfill active'
  if (deltaPerHour > 0.01) return 'growing'
  if (deltaPerHour > 0.001) return 'slow'
  return 'idle'
}

function backfillStatusText(status: BacktestProgress['backfill_status'] | null): string {
  if (!status) return 'Backfill status unavailable'
  if (status.state === 'running') return 'Backfill active now'
  if (status.state === 'idle') return 'Backfill worker idle'
  if (status.state === 'error') return 'Backfill worker reported error'
  return 'Backfill status unavailable'
}

function backfillStatusTone(status: BacktestProgress['backfill_status'] | null): string {
  if (!status) return 'text-muted'
  if (status.state === 'running') return 'text-good'
  if (status.state === 'error') return 'text-bad'
  if (status.state === 'idle') return 'text-warn'
  return 'text-muted'
}

export default function BacktestsPage() {
  const [rows, setRows] = useState<Backtest[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyKey>('StrategyBreakoutRetest')
  const [progress, setProgress] = useState<BacktestProgress | null>(null)
  const [progressError, setProgressError] = useState<string | null>(null)
  const [progressHistory, setProgressHistory] = useState<ProgressSnapshot[]>([])
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
      await Promise.all([loadBacktests(true), loadReadinessForAll(), loadProgress()])
    } finally {
      setRefreshing(false)
    }
  }

  async function loadProgress() {
    try {
      const data = await apiFetch<BacktestProgress>('/api/backtests/progress')
      setProgress(data)
      setProgressError(null)
    } catch (err) {
      setProgress(null)
      setProgressError(err instanceof Error ? err.message : 'Progress unavailable')
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
    void Promise.all([loadBacktests(), loadReadinessForAll(), loadProgress()])
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const raw = window.localStorage.getItem(PROGRESS_HISTORY_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        const sanitized = parsed
          .map((item) => sanitizeProgressSnapshot(item))
          .filter(Boolean) as ProgressSnapshot[]
        setProgressHistory(sanitized)
      }
    } catch {
      setProgressHistory([])
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined' || !progress?.generated_at) return
      const snapshot: ProgressSnapshot = {
        ts: progress.generated_at,
        timeframes: progress.timeframes.map((item) => ({
          timeframe: item.timeframe,
          candles: item.candles,
          latest_ts: item.latest_ts
        })),
        strategies: progress.strategies.map((item) => ({
          strategy: item.strategy,
          effective_ratio: item.effective_ratio,
        required_ratio: item.required_ratio
      }))
    }

    setProgressHistory((prev) => {
      const withoutDuplicateTs = prev.filter((item) => item.ts !== snapshot.ts)
      const next = [...withoutDuplicateTs, snapshot].slice(-MAX_PROGRESS_SNAPSHOTS)
      try {
        window.localStorage.setItem(PROGRESS_HISTORY_KEY, JSON.stringify(next))
      } catch {}
      return next
    })
  }, [progress])

  const progressInsights = useMemo(() => {
    const byStrategy = new Map<
      StrategyKey,
      {
        deltaPerHour: number
        etaMinutes: number | null
        candleDelta: number
      }
    >()

    for (const strategy of STRATEGIES) {
      const points = progressHistory
        .map((snapshot) => {
          const item = (snapshot.strategies ?? []).find((entry) => entry.strategy === strategy.key)
          return item ? { ts: snapshot.ts, effective_ratio: item.effective_ratio, required_ratio: item.required_ratio } : null
        })
        .filter(Boolean) as Array<{ ts: string; effective_ratio: number; required_ratio: number }>

      if (points.length < 2) {
        byStrategy.set(strategy.key, { deltaPerHour: 0, etaMinutes: null, candleDelta: 0 })
        continue
      }

      const first = points[0]
      const last = points[points.length - 1]
      const hours = Math.max(
        (new Date(last.ts).getTime() - new Date(first.ts).getTime()) / 3_600_000,
        1 / 60
      )
      const deltaPerHour = (last.effective_ratio - first.effective_ratio) / hours
      const remaining = Math.max(0, last.required_ratio - last.effective_ratio)
      const etaMinutes = deltaPerHour > 0 ? (remaining / deltaPerHour) * 60 : null
      const timeframePoints = progressHistory
        .map((snapshot) => (snapshot.timeframes ?? []).find((item) => item.timeframe === '5m'))
        .filter(Boolean) as Array<{ timeframe: string; candles: number; latest_ts: string | null }>
      const candleDelta =
        timeframePoints.length >= 2 ? timeframePoints[timeframePoints.length - 1].candles - timeframePoints[0].candles : 0

      byStrategy.set(strategy.key, { deltaPerHour, etaMinutes, candleDelta })
    }

    return byStrategy
  }, [progressHistory])

  const timeframeInsights = useMemo(() => {
    const byTimeframe = new Map<
      string,
      {
        candleDelta: number
        latestMoved: boolean
      }
    >()

    for (const timeframe of ['5m', '15m', '1h']) {
      const points = progressHistory
        .map((snapshot) => (snapshot.timeframes ?? []).find((item) => item.timeframe === timeframe))
        .filter(Boolean) as Array<{ timeframe: string; candles: number; latest_ts: string | null }>

      if (points.length < 2) {
        byTimeframe.set(timeframe, { candleDelta: 0, latestMoved: false })
        continue
      }

      byTimeframe.set(timeframe, {
        candleDelta: points[points.length - 1].candles - points[0].candles,
        latestMoved: points[0].latest_ts !== points[points.length - 1].latest_ts
      })
    }

    return byTimeframe
  }, [progressHistory])

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
    const timer = window.setInterval(() => {
      void Promise.all([loadBacktests(), loadReadinessForAll(), loadProgress()])
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [])

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

      {progress ? (
        <div className="card p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold">History Progress</div>
              <div className="text-xs text-muted">
                Ready strategies: {progress.summary.ready_strategies}/{progress.summary.total_strategies}
                {progress.generated_at ? ` | Checked ${new Date(progress.generated_at).toLocaleTimeString()}` : ''}
              </div>
            </div>
            <div className="text-right">
              <div className={`text-sm font-semibold ${backfillStatusTone(progress.backfill_status)}`}>
                {backfillStatusText(progress.backfill_status)}
              </div>
              <div className={`text-sm font-semibold ${progress.summary.all_ready ? 'text-good' : 'text-warn'}`}>
                {progress.summary.all_ready ? 'All strategies ready' : `${progress.summary.not_ready_strategies} strategy not ready`}
              </div>
              <div className="text-xs text-muted">
                Updated:{' '}
                {progress.backfill_status?.updated_at
                  ? new Date(progress.backfill_status.updated_at).toLocaleTimeString()
                  : '-'}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {progress.timeframes.map((item) => (
              <div key={item.timeframe} className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">{item.timeframe}</div>
                <div className="mt-1 text-sm">Candles: <span className="font-semibold">{item.candles}</span></div>
                <div className="text-sm">Pairs: <span className="font-semibold">{item.instruments}</span></div>
                <div className={`text-sm ${timeframeInsights.get(item.timeframe)?.candleDelta ? 'text-good' : 'text-muted'}`}>
                  Delta: {timeframeInsights.get(item.timeframe)?.candleDelta ?? 0 > 0 ? '+' : ''}
                  {timeframeInsights.get(item.timeframe)?.candleDelta ?? 0} candles
                </div>
                <div className="text-xs text-muted mt-1">
                  Latest: {item.latest_ts ? new Date(item.latest_ts).toLocaleString() : '-'}
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            {progress.strategies.map((item) => (
              <div key={item.strategy} className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">{STRATEGIES.find((strategy) => strategy.key === item.strategy)?.label || item.strategy}</div>
                <div className={`mt-1 text-sm font-semibold ${item.ready ? 'text-good' : 'text-bad'}`}>
                  {item.ready ? 'Ready' : item.reason}
                </div>
                <div className="text-sm">
                  Coverage {(item.effective_ratio * 100).toFixed(1)}% / {(item.required_ratio * 100).toFixed(1)}%
                </div>
                <div className={`text-sm ${progressTone(progressInsights.get(item.strategy)?.deltaPerHour ?? 0, progressInsights.get(item.strategy)?.candleDelta ?? 0)}`}>
                  Trend: {progressLabel(progressInsights.get(item.strategy)?.deltaPerHour ?? 0, progressInsights.get(item.strategy)?.candleDelta ?? 0)}
                </div>
                <div className="text-sm">
                  Data delta: {(progressInsights.get(item.strategy)?.candleDelta ?? 0) > 0 ? '+' : ''}
                  {progressInsights.get(item.strategy)?.candleDelta ?? 0} candles
                </div>
                <div className="text-sm">
                  ETA to target: {formatEtaMinutes(progressInsights.get(item.strategy)?.etaMinutes ?? null)}
                </div>
                <div className="text-xs text-muted mt-1">
                  {item.selected_top5.length ? item.selected_top5.join(', ') : 'No symbols selected yet'}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : progressError ? (
        <div className="card p-4 text-sm text-bad whitespace-pre-wrap">
          History progress unavailable.
          {'\n'}
          {progressError}
        </div>
      ) : null}

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
        const diagnostics =
          latestRow?.metrics_json && typeof latestRow.metrics_json.diagnostics === 'object'
            ? (latestRow.metrics_json.diagnostics as Record<string, unknown>)
            : null
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

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Signals / Deals</div>
                <div className="mt-2 text-sm">
                  Signals: <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['signals_generated']))}</span>
                </div>
                <div className="text-sm">
                  Deals: <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['trades_executed']))}</span>
                </div>
                <div className="text-sm">
                  Conversion:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['signal_to_trade_conversion']), (num) => `${(num * 100).toFixed(1)}%`)}
                  </span>
                </div>
                <div className="text-sm">
                  Missed fills:{' '}
                  <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['signals_missed_next_candle']))}</span>
                </div>
              </div>

              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Entry / Exit Quality</div>
                <div className="mt-2 text-sm">
                  Consistency:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['entry_exit_validation', 'consistency_rate']), (num) => `${(num * 100).toFixed(1)}%`)}
                  </span>
                </div>
                <div className="text-sm">
                  Sequence errors:{' '}
                  <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['entry_exit_validation', 'sequence_errors']))}</span>
                </div>
                <div className="text-sm">
                  Price errors:{' '}
                  <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['entry_exit_validation', 'price_errors']))}</span>
                </div>
              </div>

              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Stability / Duplicates</div>
                <div className="mt-2 text-sm">
                  Timeout exits:{' '}
                  <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['stability', 'timeout_exits']))}</span>
                </div>
                <div className="text-sm">
                  Duplicates:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['stability', 'duplicate_entries']))}/
                    {metricText(diagnosticsValue(diagnostics, ['stability', 'duplicate_exits']))}
                  </span>
                </div>
                <div className="text-sm">
                  Overlaps:{' '}
                  <span className="font-semibold">{metricText(diagnosticsValue(diagnostics, ['stability', 'overlapping_positions']))}</span>
                </div>
              </div>

              <div className="rounded-lg border border-line bg-panelSoft p-3">
                <div className="text-xs uppercase text-muted">Real Fees / Slippage</div>
                <div className="mt-2 text-sm">
                  Fees:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['execution_costs', 'total_fees_quote']), (num) => num.toFixed(4))}
                  </span>
                </div>
                <div className="text-sm">
                  Slippage:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['execution_costs', 'total_slippage_quote']), (num) => num.toFixed(4))}
                  </span>
                </div>
                <div className="text-sm">
                  Cost %:{' '}
                  <span className="font-semibold">
                    {metricText(diagnosticsValue(diagnostics, ['execution_costs', 'realized_cost_pct_of_notional']), (num) => `${num.toFixed(3)}%`)}
                  </span>
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
                          diagnostics: latestRow.metrics_json?.diagnostics ?? null,
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
