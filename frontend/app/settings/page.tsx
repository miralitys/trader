'use client'

import { useEffect, useState } from 'react'

import { apiFetch } from '@/lib/api'

type ScalarSettingValue = string | number | boolean

type Settings = {
  paper_enabled: boolean
  live_enabled: boolean
  live_confirmed: boolean
  risk_params_json: Record<string, ScalarSettingValue>
  strategy_params_json: Record<string, unknown>
  universe_json: Record<string, unknown>
  fees_json: Record<string, number>
  kill_switch_paused: boolean
  strict_mode: boolean
  coinbase_api_key_hint?: string | null
}

type MessageResponse = {
  message: string
}

const GENERAL_STRATEGY_KEYS = ['ema200_filter_1h', 'atr_threshold_pct_1h', 'confirm_15m', 'trade_only_strategy']
const BREAKOUT_STRATEGY_KEYS = ['breakout_lookback', 'breakout_retest_k_atr']
const PULLBACK_STRATEGY_KEYS = ['pullback_rsi_threshold']
const MR_STRATEGY_KEYS = [
  'mr_bb_period',
  'mr_bb_std',
  'mr_rsi_period',
  'mr_rsi_entry_threshold',
  'mr_safety_ema_period',
  'mr_lookback_stop',
  'mr_stop_atr_buffer',
  'mr_max_stop_pct',
  'mr_tp_rr'
]
const TREND_RETRACE_70_KEYS = [
  'tr70_ema_fast_period',
  'tr70_ema_mid_period',
  'tr70_ema_slow_period',
  'tr70_pullback_lookback',
  'tr70_pullback_depth_pct',
  'tr70_reclaim_buffer_pct',
  'tr70_rsi_period',
  'tr70_rsi_min',
  'tr70_rsi_max',
  'tr70_stop_atr_mult',
  'tr70_min_stop_pct',
  'tr70_max_stop_pct',
  'tr70_tp_rr',
  'tr70_min_volume_ratio'
]

const RESERVED_STRATEGY_KEYS = new Set([
  ...GENERAL_STRATEGY_KEYS,
  ...BREAKOUT_STRATEGY_KEYS,
  ...PULLBACK_STRATEGY_KEYS,
  ...MR_STRATEGY_KEYS,
  ...TREND_RETRACE_70_KEYS,
  'strategy_presets'
])

function isScalarSettingValue(value: unknown): value is ScalarSettingValue {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [liveConfirmationText, setLiveConfirmationText] = useState('')
  const [coinbaseKey, setCoinbaseKey] = useState('')
  const [coinbaseSecret, setCoinbaseSecret] = useState('')
  const [paperLimitUsd, setPaperLimitUsd] = useState('10000')
  const [resettingPaper, setResettingPaper] = useState(false)

  function updateStrategyParam(key: string, value: string) {
    setSettings((prev) => {
      if (!prev) return prev
      const current = prev.strategy_params_json[key]
      if (!isScalarSettingValue(current)) return prev

      let parsed: ScalarSettingValue = value
      if (typeof current === 'number') parsed = Number(value)
      if (typeof current === 'boolean') parsed = value === 'true'

      return {
        ...prev,
        strategy_params_json: {
          ...prev.strategy_params_json,
          [key]: parsed
        }
      }
    })
  }

  function renderStrategyParamInput(key: string) {
    const val = settings?.strategy_params_json[key]
    if (!isScalarSettingValue(val)) return null
    return (
      <div key={key}>
        <label className="text-xs text-muted">{key}</label>
        <input
          className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
          value={String(val)}
          onChange={(e) => updateStrategyParam(key, e.target.value)}
        />
      </div>
    )
  }

  async function load() {
    try {
      const data = await apiFetch<Settings>('/api/settings')
      setSettings(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function save() {
    if (!settings) return
    setError(null)
    setSuccess(null)
    try {
      const payload = {
        paper_enabled: settings.paper_enabled,
        live_enabled: settings.live_enabled,
        live_confirmation_text: settings.live_enabled ? liveConfirmationText : undefined,
        risk_params_json: settings.risk_params_json,
        strategy_params_json: settings.strategy_params_json,
        universe_json: settings.universe_json,
        fees_json: settings.fees_json,
        strict_mode: settings.strict_mode,
        coinbase_api_key: coinbaseKey || undefined,
        coinbase_api_secret: coinbaseSecret || undefined
      }
      const updated = await apiFetch<Settings>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify(payload)
      })
      setSettings(updated)
      setCoinbaseKey('')
      setCoinbaseSecret('')
      setSuccess('Settings saved')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    }
  }

  async function resetPaperState() {
    const limit = Number(paperLimitUsd)
    if (!Number.isFinite(limit) || limit <= 0) {
      setError('Paper limit must be a positive number')
      return
    }

    setResettingPaper(true)
    setError(null)
    setSuccess(null)
    try {
      const resp = await apiFetch<MessageResponse>('/api/system/paper/reset', {
        method: 'POST',
        body: JSON.stringify({ limit_usd: limit })
      })
      await load()
      setSuccess(resp.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset paper state')
    } finally {
      setResettingPaper(false)
    }
  }

  async function setSystemPaused(paused: boolean) {
    setError(null)
    setSuccess(null)
    try {
      const endpoint = paused ? '/api/system/pause' : '/api/system/resume'
      const resp = await apiFetch<MessageResponse>(endpoint, { method: 'POST' })
      await load()
      setSuccess(resp.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change kill switch state')
    }
  }

  if (!settings) {
    return <div>Loading settings...</div>
  }

  const otherStrategyKeys = Object.keys(settings.strategy_params_json).filter(
    (key) => !RESERVED_STRATEGY_KEYS.has(key) && isScalarSettingValue(settings.strategy_params_json[key])
  )

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted">Execution mode, credentials and legacy config values.</p>
      </div>

      <div className="card p-3 text-sm">
        Backtests now run only through the 4 fixed strategies on the Backtests page. They are independent from each
        other and do not use `Risk params (legacy)` or `Strategy params / Fees (legacy)` below.
      </div>

      {error ? <div className="card p-3 text-bad text-sm whitespace-pre-wrap">{error}</div> : null}
      {success ? <div className="card p-3 text-good text-sm">{success}</div> : null}

      <div className="card p-4 space-y-4">
        <h2 className="font-semibold">Execution mode</h2>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={settings.paper_enabled}
            onChange={(e) => setSettings((prev) => (prev ? { ...prev, paper_enabled: e.target.checked } : prev))}
          />
          Paper enabled (default ON)
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={settings.live_enabled}
            onChange={(e) => setSettings((prev) => (prev ? { ...prev, live_enabled: e.target.checked } : prev))}
          />
          Live enabled (requires explicit confirmation)
        </label>

        {settings.live_enabled ? (
          <div>
            <label className="text-xs text-muted">Type exactly: ENABLE LIVE</label>
            <input
              className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full max-w-md"
              value={liveConfirmationText}
              onChange={(e) => setLiveConfirmationText(e.target.value)}
              placeholder="ENABLE LIVE"
            />
          </div>
        ) : null}

        <div className="text-xs text-muted">
          Existing Coinbase key hint: {settings.coinbase_api_key_hint || 'not set'}
        </div>

        <div className="pt-2 border-t border-line" />
        <div className="text-sm">
          <span className="text-muted">Kill switch:</span>{' '}
          <span className={settings.kill_switch_paused ? 'text-bad font-semibold' : 'text-good font-semibold'}>
            {settings.kill_switch_paused ? 'PAUSED' : 'ACTIVE'}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded-lg border border-line bg-panel px-3 py-2 text-sm"
            onClick={() => void setSystemPaused(true)}
            disabled={settings.kill_switch_paused}
          >
            Pause
          </button>
          <button
            type="button"
            className="rounded-lg border border-line bg-panel px-3 py-2 text-sm"
            onClick={() => void setSystemPaused(false)}
            disabled={!settings.kill_switch_paused}
          >
            Resume
          </button>
        </div>
      </div>

      <div className="card p-4 space-y-4">
        <h2 className="font-semibold">Coinbase API credentials</h2>
        <p className="text-xs text-muted">
          Raw keys are never returned in API responses. Store encrypted in DB only with SECRET_ENCRYPTION_KEY.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted">API key</label>
            <input
              className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
              value={coinbaseKey}
              onChange={(e) => setCoinbaseKey(e.target.value)}
              placeholder="organizations/.../apiKeys/..."
            />
          </div>
          <div>
            <label className="text-xs text-muted">API secret</label>
            <textarea
              className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full h-24"
              value={coinbaseSecret}
              onChange={(e) => setCoinbaseSecret(e.target.value)}
              placeholder="API secret"
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="card p-4 space-y-3">
          <h2 className="font-semibold">Risk params (legacy)</h2>
          {Object.entries(settings.risk_params_json).map(([key, val]) => (
            <div key={key}>
              <label className="text-xs text-muted">{key}</label>
              <input
                className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
                value={String(val)}
                onChange={(e) => {
                  const value = e.target.value
                  setSettings((prev) => {
                    if (!prev) return prev
                    const current = prev.risk_params_json[key]
                    let parsed: ScalarSettingValue = value
                    if (typeof current === 'number') parsed = Number(value)
                    if (typeof current === 'boolean') parsed = value === 'true'
                    return {
                      ...prev,
                      risk_params_json: {
                        ...prev.risk_params_json,
                        [key]: parsed
                      }
                    }
                  })
                }}
              />
            </div>
          ))}
        </div>

        <div className="card p-4 space-y-3">
          <h2 className="font-semibold">Strategy params / Fees (legacy)</h2>
          <div className="text-xs uppercase tracking-wide text-muted">General / Regime</div>
          {GENERAL_STRATEGY_KEYS.map((key) => renderStrategyParamInput(key))}

          <div className="pt-2 border-t border-line" />
          <div className="text-xs uppercase tracking-wide text-muted">BreakoutRetest</div>
          {BREAKOUT_STRATEGY_KEYS.map((key) => renderStrategyParamInput(key))}

          <div className="pt-2 border-t border-line" />
          <div className="text-xs uppercase tracking-wide text-muted">PullbackToTrend</div>
          {PULLBACK_STRATEGY_KEYS.map((key) => renderStrategyParamInput(key))}

          <div className="pt-2 border-t border-line" />
          <div className="text-xs uppercase tracking-wide text-muted">MeanReversionHardStop</div>
          {MR_STRATEGY_KEYS.map((key) => renderStrategyParamInput(key))}

          <div className="pt-2 border-t border-line" />
          <div className="text-xs uppercase tracking-wide text-muted">TrendRetrace70</div>
          {TREND_RETRACE_70_KEYS.map((key) => renderStrategyParamInput(key))}

          {otherStrategyKeys.length ? (
            <>
              <div className="pt-2 border-t border-line" />
              <div className="text-xs uppercase tracking-wide text-muted">Other strategy params</div>
              {otherStrategyKeys.map((key) => renderStrategyParamInput(key))}
            </>
          ) : null}

          <div className="pt-2 border-t border-line" />

          {Object.entries(settings.fees_json).map(([key, val]) => (
            <div key={key}>
              <label className="text-xs text-muted">{key}</label>
              <input
                className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
                value={String(val)}
                onChange={(e) => {
                  const value = Number(e.target.value)
                  setSettings((prev) =>
                    prev
                      ? {
                          ...prev,
                          fees_json: {
                            ...prev.fees_json,
                            [key]: value
                          }
                        }
                      : prev
                  )
                }}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <button className="rounded-lg bg-accent text-white px-4 py-2 text-sm" onClick={save}>
          Save settings
        </button>
        <button className="rounded-lg border border-line bg-panel px-4 py-2 text-sm" onClick={load}>
          Reload
        </button>
      </div>

      <div className="card p-4 space-y-3">
        <h2 className="font-semibold">Paper reset</h2>
        <p className="text-xs text-muted">
          Deletes all trades, open positions, orders and equity snapshots, then sets paper limit.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="text-xs text-muted">Paper limit ($)</label>
            <input
              className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-40"
              value={paperLimitUsd}
              onChange={(e) => setPaperLimitUsd(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="rounded-lg border border-line bg-panel px-4 py-2 text-sm disabled:opacity-50"
            onClick={resetPaperState}
            disabled={resettingPaper}
          >
            {resettingPaper ? 'Resetting...' : 'Reset paper history and open trades'}
          </button>
        </div>
      </div>
    </div>
  )
}
