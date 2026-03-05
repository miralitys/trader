'use client'

import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'

import { LoginForm } from '@/components/login-form'
import { Sidebar } from '@/components/sidebar'
import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '@/lib/auth'

export function AppShell({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false)
  const pathname = usePathname()
  const router = useRouter()

  useEffect(() => {
    setAuthenticated(Boolean(getToken()))
  }, [pathname])

  useEffect(() => {
    const onAuthExpired = () => {
      clearToken()
      setAuthenticated(false)
      router.push('/overview')
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onAuthExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onAuthExpired)
  }, [router])

  if (!authenticated) {
    return <LoginForm onAuthenticated={() => setAuthenticated(true)} />
  }

  return (
    <div className="min-h-screen flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <div className="mb-4 flex items-center justify-end">
          <button
            className="rounded-lg border border-line bg-panel px-3 py-1 text-sm"
            onClick={() => {
              clearToken()
              setAuthenticated(false)
              router.push('/overview')
            }}
          >
            Logout
          </button>
        </div>
        {children}
      </main>
    </div>
  )
}
