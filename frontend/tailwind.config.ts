import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./app/**/*.{js,ts,jsx,tsx}', './components/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        panel: 'var(--panel)',
        panelSoft: 'var(--panel-soft)',
        line: 'var(--line)',
        ink: 'var(--ink)',
        muted: 'var(--muted)',
        good: 'var(--good)',
        bad: 'var(--bad)',
        warn: 'var(--warn)',
        accent: 'var(--accent)'
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Segoe UI"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'monospace']
      }
    }
  },
  plugins: []
}

export default config
