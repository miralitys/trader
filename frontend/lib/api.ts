'use client'

import { getToken } from './auth'

// Always use same-origin API routes from the browser.
// Next.js handles forwarding to backend in app/api/[...path]/route.ts.
const API_BASE = ''

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string> | undefined)
  }

  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    cache: 'no-store'
  })

  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || `Request failed: ${resp.status}`)
  }

  if (resp.status === 204) {
    return null as T
  }

  return (await resp.json()) as T
}

export const apiBase = API_BASE
