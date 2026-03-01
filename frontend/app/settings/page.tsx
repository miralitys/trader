'use client'

import { useEffect, useState } from 'react'

import { apiFetch } from '@/lib/api'

type Settings = {
  paper_enabled: boolean
  live_enabled: boolean
  live_confirmed: boolean
  risk_params_json: Record<string, number | boolean | string>
  strategy_params_json: Record<string, number | boolean | string>
  universe_json: Record<string, unknown>
  fees_json: Record<string, number>
  kill_switch_paused: boolean
  strict_mode: boolean
  coinbase_api_key_hint?: string | null
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [liveConfirmationText, setLiveConfirmationText] = useState('')
  const [coinbaseKey, setCoinbaseKey] = useState('')
  const [coinbaseSecret, setCoinbaseSecret] = useState('')

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

  if (!settings) {
    return <div>Loading settings...</div>
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted">Execution mode, risk controls, strategy filters and Coinbase credentials.</p>
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
      </div>

      <div className="card p-4 space-y-4">
        <h2 className="font-semibold">Coinbase API credentials</h2>
        <p className="text-xs text-muted">Raw keys are never returned in API responses. Store encrypted in DB only with SECRET_ENCRYPTION_KEY.</p>
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
          <h2 className="font-semibold">Risk params</h2>
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
                    let parsed: string | number | boolean = value
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
          <h2 className="font-semibold">Strategy params / Fees</h2>
          {Object.entries(settings.strategy_params_json).map(([key, val]) => (
            <div key={key}>
              <label className="text-xs text-muted">{key}</label>
              <input
                className="block mt-1 rounded-lg border border-line bg-panelSoft px-2 py-1 text-sm w-full"
                value={String(val)}
                onChange={(e) => {
                  const value = e.target.value
                  setSettings((prev) => {
                    if (!prev) return prev
                    const current = prev.strategy_params_json[key]
                    let parsed: string | number | boolean = value
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
                }}
              />
            </div>
          ))}

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
    </div>
  )
}
