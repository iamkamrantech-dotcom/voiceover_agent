import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api/client'
import { useLive } from './api/useLive'
import { ToastProvider } from './components/Toast'
import SignIn from './pages/SignIn'
import { applyTheme, readTheme, type Theme } from './lib/theme'
import Overview from './pages/Overview'
import NewVideo from './pages/NewVideo'
import Jobs from './pages/Jobs'
import JobDetail from './pages/JobDetail'
import Voice from './pages/Voice'
import Settings from './pages/Settings'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/new', label: 'New video', end: false },
  { to: '/jobs', label: 'Jobs', end: false },
  { to: '/voice', label: 'Voice', end: false },
  { to: '/settings', label: 'Settings', end: false },
]

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const next: Record<Theme, Theme> = {
    system: 'light',
    light: 'dark',
    dark: 'system',
  }
  const label: Record<Theme, string> = {
    system: 'System',
    light: 'Light',
    dark: 'Dark',
  }

  return (
    <button
      onClick={() => setTheme(next[theme])}
      className="h-8 rounded-md border border-border px-2.5 text-sm text-muted hover:bg-raised hover:text-text"
      aria-label={`Theme: ${label[theme]}. Switch to ${label[next[theme]]}`}
      data-testid="theme-toggle"
    >
      {label[theme]}
    </button>
  )
}

function QueueStatus() {
  const { queue, connected } = useLive()
  const running = Boolean(queue.running)

  return (
    <div className="flex items-center gap-3 text-sm" data-testid="queue-status">
      <span className="flex items-center gap-1.5">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{
            background: running
              ? 'var(--accent)'
              : connected
                ? 'var(--success)'
                : 'var(--faint)',
          }}
          aria-hidden="true"
        />
        <span className={running ? 'text-text' : 'text-muted'}>
          {running ? 'Processing' : connected ? 'Idle' : 'Reconnecting'}
        </span>
      </span>
      {queue.queued > 0 && (
        <span className="tnum text-muted">{queue.queued} queued</span>
      )}
    </div>
  )
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <div className="px-2 pb-3 pt-1">
        <span className="text-base font-semibold">Voiceover Agent</span>
      </div>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            `rounded-md px-2.5 py-2 text-base transition-colors ${
              isActive
                ? 'bg-accent-wash font-medium text-accent'
                : 'text-muted hover:bg-raised hover:text-text'
            }`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [gate, setGate] = useState<'checking' | 'locked' | 'open'>('checking')
  const location = useLocation()

  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  // Only gates when APP_PASSWORD is set; otherwise the app opens straight up.
  useEffect(() => {
    api
      .session()
      .then((s) => setGate(!s.required || s.signed_in ? 'open' : 'locked'))
      .catch(() => setGate('open'))
  }, [])

  if (gate === 'checking') return null
  if (gate === 'locked') {
    return (
      <ToastProvider>
        <SignIn onSignedIn={() => setGate('open')} />
      </ToastProvider>
    )
  }

  return (
    <ToastProvider>
      <div className="flex h-full">
        {/* Desktop sidebar */}
        <aside className="hidden w-60 shrink-0 border-r border-border bg-surface lg:block">
          <Sidebar />
        </aside>

        {/* Mobile drawer */}
        {menuOpen && (
          <div
            className="fixed inset-0 z-40 lg:hidden"
            style={{ background: 'rgb(0 0 0 / 0.45)' }}
            onClick={() => setMenuOpen(false)}
          >
            <aside
              className="h-full w-60 border-r border-border bg-surface"
              onClick={(e) => e.stopPropagation()}
            >
              <Sidebar onNavigate={() => setMenuOpen(false)} />
            </aside>
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
            <button
              className="h-8 rounded-md border border-border px-2.5 text-sm text-muted lg:hidden"
              onClick={() => setMenuOpen(true)}
              aria-label="Open menu"
            >
              Menu
            </button>
            <div className="flex-1" />
            <QueueStatus />
            <ThemeToggle />
          </header>

          <main className="min-w-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1100px] p-4 sm:p-6">
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/new" element={<NewVideo />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/jobs/:id" element={<JobDetail />} />
                <Route path="/voice" element={<Voice />} />
                <Route path="/settings" element={<Settings />} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
    </ToastProvider>
  )
}
