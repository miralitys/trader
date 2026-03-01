import './globals.css'

import type { Metadata } from 'next'

import { AppShell } from '@/components/app-shell'

export const metadata: Metadata = {
  title: 'Trader Panel',
  description: 'Paper-first autotrading panel for Coinbase Advanced Trade'
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
