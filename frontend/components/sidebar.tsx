'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const links = [
  { href: '/overview', label: 'Overview' },
  { href: '/signals', label: 'Signals' },
  { href: '/chart', label: 'Chart' },
  { href: '/backtests', label: 'Backtests' },
  { href: '/strategy-stats', label: 'Strategy Stats' },
  { href: '/trading', label: 'Trading' },
  { href: '/settings', label: 'Settings' }
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="w-56 shrink-0 border-r border-line bg-panel/90 p-4 backdrop-blur">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-wide text-muted">Trader</div>
        <div className="text-lg font-semibold">Control Panel</div>
      </div>
      <nav className="space-y-1">
        {links.map((link) => {
          const active = pathname === link.href || pathname?.startsWith(`${link.href}/`)
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`block rounded-lg px-3 py-2 text-sm transition ${
                active ? 'bg-accent text-white' : 'text-ink hover:bg-panelSoft'
              }`}
            >
              {link.label}
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
