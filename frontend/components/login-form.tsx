'use client'

import { FormEvent, useState } from 'react'

import { apiFetch } from '@/lib/api'
import { setToken } from '@/lib/auth'

type Props = {
  onAuthenticated: () => void
}

export function LoginForm({ onAuthenticated }: Props) {
  const [email, setEmail] = useState('admin@example.com')
  const [password, setPassword] = useState('admin-password-123')
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)

    try {
      if (mode === 'signup') {
        await apiFetch('/api/auth/signup', {
          method: 'POST',
          body: JSON.stringify({ email, password })
        })
      }
      const token = await apiFetch<{ access_token: string }>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
      })
      setToken(token.access_token)
      onAuthenticated()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="card w-full max-w-md p-6">
        <h1 className="text-2xl font-semibold">Trader Control Panel</h1>
        <p className="text-sm text-muted mt-1">Coinbase Advanced Trade automation (Paper by default).</p>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <div>
            <label className="block text-sm mb-1">Email</label>
            <input
              className="w-full rounded-lg border border-line bg-panelSoft px-3 py-2"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
            />
          </div>

          <div>
            <label className="block text-sm mb-1">Password</label>
            <input
              className="w-full rounded-lg border border-line bg-panelSoft px-3 py-2"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              minLength={8}
              required
            />
          </div>

          {error ? <div className="text-sm text-bad whitespace-pre-wrap">{error}</div> : null}

          <button
            className="w-full rounded-lg bg-accent text-white py-2 font-medium disabled:opacity-50"
            disabled={loading}
            type="submit"
          >
            {loading ? 'Please wait...' : mode === 'login' ? 'Login' : 'Create account'}
          </button>
        </form>

        <button
          className="mt-3 text-sm text-muted underline"
          onClick={() => setMode(mode === 'login' ? 'signup' : 'login')}
          type="button"
        >
          {mode === 'login' ? 'Need an account? Sign up' : 'Already have an account? Login'}
        </button>
      </div>
    </div>
  )
}
