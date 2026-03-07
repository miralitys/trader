'use client'

import { useEffect, useMemo, useRef, useState } from 'react'

import { apiFetch } from '@/lib/api'
import { strategyLabel, type BaseStrategy } from '@/lib/strategies'

type BatchRunOut = {
  batch_id: string
  start_ts: string
  end_ts: string
  strategies: string[]
  enqueue_errors: Record<string, string>
}

type BatchStrategyStats = {
  strategy: string
  status: string
  backtest_id?: number | null
  created_at?: string | null
  start_ts?: string | null
  end_ts?: string | null
  base: Record<string, unknown>
  stress_1_5x: Record<string, unknown>
  stress_2_0x: Record<string, unknown>
  error?: string | null
}

type BatchStatsOut = {
  batch_id: string
  start_ts?: string | null
  end_ts?: string | null
  summary: {
    total_strategies: number
    missing: number
    queued: number
    running: number
    completed: number
    failed: number
    cancelled: number
    other: number
    all_completed: boolean
  }
  strategies: BatchStrategyStats[]
}

const ACTIVE_BATCH_STORAGE_KEY = 'trader:strategy-stats:active-batch-id'
const POLL_INTERVAL_MS = 15_000
const AUTO_RETRY_DELAY_MS = 120_000
const QUEUED_STUCK_MINUTES = 10
const DEFAULT_COMMON_PARAMS = {
  history_min_coverage_ratio: 0.5,
  history_required_coverage_ratio: 0.5,
  history_target_coverage_ratio: 0.7,
  history_allow_degraded: true
}

const STRATEGY_ORDER: BaseStrategy[] = [
  'StrategyBreakoutRetest',
  'StrategyPullbackToTrend',
  'MeanReversionHardStop',
  'StrategyTrendRetrace70'
]

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function statusClass(status: string): string {
  if (status === 'completed') return 'text-good'
  if (status === 'running' || status === 'queued') return 'text-warn'
  if (status === 'failed' || status === 'cancelled' || status === 'missing') return 'text-bad'
  return 'text-muted'
}

function strategyDisplayName(strategy: string): string {
  return STRATEGY_ORDER.includes(strategy as BaseStrategy)
    ? strategyLabel(strategy as BaseStrategy)
    : strategy
}

function minutesSince(isoTs: string, nowMs: number): number {
  const tsMs = new Date(isoTs).getTime()
  if (!Number.isFinite(tsMs)) return 0
  return Math.max(0, Math.floor((nowMs - tsMs) / 60000))
}

export default function StrategyStatsPage() {
  const [batchId, setBatchId] = useState<string | null>(null)
  const [stats, setStats] = useState<BatchStatsOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)
  const [polling, setPolling] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  const pollInFlightRef = useRef(false)
  const retryInFlightRef = useRef(false)
  const nextRetryAllowedAtRef = useRef<number>(0)

  const hasActiveBatch = Boolean(batchId)

  async function runBatch(strategies?: string[]) {
    const body: Record<string, unknown> = {
      common_params: DEFAULT_COMMON_PARAMS
    }
    if (batchId) {
      body.batch_id = batchId
    }
    if (strategies && strategies.length) {
      body.strategies = strategies
    }

    const data = await apiFetch<BatchRunOut>('/api/backtests/run-all', {
      method: 'POST',
      body: JSON.stringify(body)
    })
    setBatchId(data.batch_id)
    localStorage.setItem(ACTIVE_BATCH_STORAGE_KEY, data.batch_id)
    return data.batch_id
  }

  async function launchAll() {
    setLaunching(true)
    setError(null)
    try {
      const newBatchId = await runBatch()
      await fetchBatchStats(newBatchId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start full strategy stats run')
    } finally {
      setLaunching(false)
    }
  }

  async function fetchBatchStats(targetBatchId?: string) {
    const id = targetBatchId || batchId
    if (!id || pollInFlightRef.current) return
    pollInFlightRef.current = true
    setPolling(true)
    try {
      const data = await apiFetch<BatchStatsOut>(`/api/backtests/batches/${encodeURIComponent(id)}/stats`)
      setStats(data)
      setLastUpdatedAt(new Date())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load batch stats')
    } finally {
      pollInFlightRef.current = false
      setPolling(false)
    }
  }

  async function retryIncompleteStrategies(target: BatchStatsOut) {
    if (retryInFlightRef.current) return
    if (target.summary.all_completed) return
    if (target.summary.running > 0 || target.summary.queued > 0) return
    if (Date.now() < nextRetryAllowedAtRef.current) return

    const pendingStrategies = target.strategies
      .filter((item) => item.status !== 'completed')
      .map((item) => item.strategy)
      .filter(Boolean)

    if (!pendingStrategies.length) return

    retryInFlightRef.current = true
    setRetrying(true)
    setError(null)
    try {
      await runBatch(pendingStrategies)
      nextRetryAllowedAtRef.current = Date.now() + AUTO_RETRY_DELAY_MS
      await fetchBatchStats(target.batch_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to retry incomplete strategies')
      nextRetryAllowedAtRef.current = Date.now() + AUTO_RETRY_DELAY_MS
    } finally {
      retryInFlightRef.current = false
      setRetrying(false)
    }
  }

  function clearBatch() {
    localStorage.removeItem(ACTIVE_BATCH_STORAGE_KEY)
    setBatchId(null)
    setStats(null)
    setError(null)
    setLastUpdatedAt(null)
  }

  useEffect(() => {
    const stored = localStorage.getItem(ACTIVE_BATCH_STORAGE_KEY)
    if (stored?.trim()) {
      setBatchId(stored.trim())
    }
  }, [])

  useEffect(() => {
    if (!batchId) return
    void fetchBatchStats(batchId)
    const timer = window.setInterval(() => {
      void fetchBatchStats(batchId)
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [batchId])

  useEffect(() => {
    if (!stats || !batchId) return
    if (stats.summary.all_completed) return
    void retryIncompleteStrategies(stats)
  }, [stats, batchId])

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const sortedStrategies = useMemo(() => {
    const map = new Map(stats?.strategies.map((item) => [item.strategy, item]) || [])
    return STRATEGY_ORDER.map((strategy) => map.get(strategy)).filter(Boolean) as BatchStrategyStats[]
  }, [stats])

  const queuedStuckInfo = useMemo(() => {
    if (!stats) return null
    if (stats.summary.queued <= 0 || stats.summary.running > 0) return null

    const queuedRows = stats.strategies.filter((item) => item.status === 'queued')
    if (!queuedRows.length) return null

    const queuedAges = queuedRows
      .map((item) => (item.created_at ? minutesSince(item.created_at, nowMs) : null))
      .filter((value): value is number => value !== null)

    if (!queuedAges.length) return null
    const oldestQueuedMin = Math.max(...queuedAges)
    if (oldestQueuedMin < QUEUED_STUCK_MINUTES) return null

    return {
      oldestQueuedMin,
      queuedCount: queuedRows.length
    }
  }, [stats, nowMs])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Strategy Stats (2y)</h1>
        <p className="text-sm text-muted">
          One-click full run for 4 strategies. The page keeps polling and auto-retries incomplete strategies until all are completed.
        </p>
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}
      {queuedStuckInfo ? (
        <div className="card p-3 text-bad text-sm">
          Queue appears stuck: {queuedStuckInfo.queuedCount} backtest(s) queued for up to {queuedStuckInfo.oldestQueuedMin}m
          with no running task. Check `trader-backtest-worker` logs and restart the worker if needed.
        </div>
      ) : null}

      <div className="card p-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-lg bg-accent text-white px-3 py-2 text-sm disabled:opacity-50"
          onClick={() => void launchAll()}
          disabled={launching || polling || retrying}
        >
          {launching ? 'Starting...' : 'Run Full Stats (4 Strategies)'}
        </button>
        <button
          type="button"
          className="rounded-lg border border-line bg-panel px-3 py-2 text-sm disabled:opacity-50"
          onClick={() => void fetchBatchStats()}
          disabled={!hasActiveBatch || polling}
        >
          {polling ? 'Refreshing...' : 'Refresh'}
        </button>
        <button
          type="button"
          className="rounded-lg border border-line bg-panel px-3 py-2 text-sm"
          onClick={clearBatch}
        >
          Clear Batch
        </button>
        <div className="text-xs text-muted ml-auto">
          {lastUpdatedAt ? `Updated ${lastUpdatedAt.toLocaleTimeString()}` : 'Not updated yet'}
          {retrying ? ' | Retrying incomplete strategies...' : null}
        </div>
      </div>

      <div className="card p-3 text-sm space-y-1">
        <div><span className="text-muted">Batch ID:</span> {batchId || '-'}</div>
        <div>
          <span className="text-muted">Coverage limits:</span> min 50% | required 50% | target 70%
          {' '}| degraded fallback: on
        </div>
        <div>
          <span className="text-muted">Period:</span>{' '}
          {stats?.start_ts ? new Date(stats.start_ts).toLocaleString() : '-'} -{' '}
          {stats?.end_ts ? new Date(stats.end_ts).toLocaleString() : '-'}
        </div>
      </div>

      {stats ? (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="card p-4">
            <div className="text-xs uppercase text-muted">Total</div>
            <div className="text-xl font-semibold">{stats.summary.total_strategies}</div>
          </div>
          <div className="card p-4">
            <div className="text-xs uppercase text-muted">Completed</div>
            <div className="text-xl font-semibold text-good">{stats.summary.completed}</div>
          </div>
          <div className="card p-4">
            <div className="text-xs uppercase text-muted">Active</div>
            <div className="text-xl font-semibold text-warn">{stats.summary.running + stats.summary.queued}</div>
          </div>
          <div className="card p-4">
            <div className="text-xs uppercase text-muted">Failed/Missing</div>
            <div className="text-xl font-semibold text-bad">
              {stats.summary.failed + stats.summary.cancelled + stats.summary.missing}
            </div>
          </div>
        </div>
      ) : null}

      <div className="card p-3 overflow-auto">
        <table className="w-full text-sm min-w-[980px]">
          <thead>
            <tr className="text-left text-muted">
              <th>Strategy</th>
              <th>Status</th>
              <th>Backtest ID</th>
              <th>Trades</th>
              <th>Winrate</th>
              <th>PF</th>
              <th>Max DD</th>
              <th>Error</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {sortedStrategies.map((row) => {
              const trades = toNumber(row.base?.trades)
              const winrate = toNumber(row.base?.winrate)
              const pf = toNumber(row.base?.profit_factor)
              const maxDd = toNumber(row.base?.max_drawdown_pct)
              return (
                <tr key={row.strategy} className="border-t border-line">
                  <td className="py-2 font-medium">{strategyDisplayName(row.strategy)}</td>
                  <td className={statusClass(row.status)}>{row.status}</td>
                  <td>{row.backtest_id ?? '-'}</td>
                  <td>{trades ?? '-'}</td>
                  <td>{winrate !== null ? `${(winrate * 100).toFixed(1)}%` : '-'}</td>
                  <td>{pf !== null ? pf.toFixed(2) : '-'}</td>
                  <td>{maxDd !== null ? `${maxDd.toFixed(2)}%` : '-'}</td>
                  <td className="max-w-[260px] truncate">{row.error || '-'}</td>
                  <td>{row.created_at ? new Date(row.created_at).toLocaleString() : '-'}</td>
                </tr>
              )
            })}
            {!sortedStrategies.length ? (
              <tr className="border-t border-line">
                <td className="py-3 text-muted" colSpan={9}>
                  Start a full run to see strategy stats.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  )
}
