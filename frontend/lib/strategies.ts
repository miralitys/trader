export type BaseStrategy =
  | 'StrategyBreakoutRetest'
  | 'StrategyPullbackToTrend'
  | 'MeanReversionHardStop'
  | 'StrategyTrendRetrace70'

export type StrategyBacktestParams = {
  history_min_coverage_ratio?: number
  history_target_coverage_ratio?: number
  history_required_coverage_ratio?: number
  input_tickers?: string[]
}

export type StrategyPreset = {
  name: string
  base_strategy: BaseStrategy
  backtest_params?: StrategyBacktestParams
}

export const BUILTIN_STRATEGY_OPTIONS: Array<{ value: BaseStrategy; label: string }> = [
  { value: 'StrategyBreakoutRetest', label: 'BreakoutRetest' },
  { value: 'StrategyPullbackToTrend', label: 'PullbackToTrend' },
  { value: 'MeanReversionHardStop', label: 'MeanReversionHardStop' },
  { value: 'StrategyTrendRetrace70', label: 'TrendRetrace70' }
]

const BUILTIN_STRATEGY_LABELS: Record<BaseStrategy, string> = BUILTIN_STRATEGY_OPTIONS.reduce(
  (acc, option) => {
    acc[option.value] = option.label
    return acc
  },
  {} as Record<BaseStrategy, string>
)

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function normalizeTickers(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  const seen = new Set<string>()
  const normalized: string[] = []
  for (const item of value) {
    const ticker = String(item).trim().toUpperCase()
    if (!ticker || seen.has(ticker)) continue
    seen.add(ticker)
    normalized.push(ticker)
  }
  return normalized.length ? normalized : undefined
}

function normalizeBacktestParams(value: unknown): StrategyBacktestParams | undefined {
  if (!isObjectRecord(value)) return undefined

  const minCoverage =
    typeof value.history_min_coverage_ratio === 'number'
      ? value.history_min_coverage_ratio
      : value.min_coverage_ratio
  const targetCoverage =
    typeof value.history_target_coverage_ratio === 'number'
      ? value.history_target_coverage_ratio
      : value.target_coverage_ratio
  const requiredCoverage =
    typeof value.history_required_coverage_ratio === 'number'
      ? value.history_required_coverage_ratio
      : value.required_coverage_ratio
  const inputTickers = normalizeTickers(value.input_tickers)

  const normalized: StrategyBacktestParams = {}
  if (typeof minCoverage === 'number' && Number.isFinite(minCoverage)) {
    normalized.history_min_coverage_ratio = minCoverage
  }
  if (typeof targetCoverage === 'number' && Number.isFinite(targetCoverage)) {
    normalized.history_target_coverage_ratio = targetCoverage
  }
  if (typeof requiredCoverage === 'number' && Number.isFinite(requiredCoverage)) {
    normalized.history_required_coverage_ratio = requiredCoverage
  }
  if (inputTickers) {
    normalized.input_tickers = inputTickers
  }

  return Object.keys(normalized).length ? normalized : undefined
}

export function isBaseStrategy(value: unknown): value is BaseStrategy {
  return (
    typeof value === 'string' &&
    BUILTIN_STRATEGY_OPTIONS.some((option) => option.value === value)
  )
}

export function parseStrategyPresets(raw: unknown): StrategyPreset[] {
  if (!Array.isArray(raw)) return []

  const seen = new Set<string>()
  const result: StrategyPreset[] = []

  for (const item of raw) {
    if (!isObjectRecord(item)) continue
    const name = typeof item.name === 'string' ? item.name.trim() : ''
    const base = item.base_strategy
    if (!name || !isBaseStrategy(base)) continue

    const dedupeKey = name.toLowerCase()
    if (seen.has(dedupeKey)) continue
    seen.add(dedupeKey)
    result.push({
      name,
      base_strategy: base,
      backtest_params: normalizeBacktestParams(item.backtest_params)
    })
  }

  return result
}

export function strategyLabel(value: BaseStrategy): string {
  return BUILTIN_STRATEGY_LABELS[value]
}
