import { useEffect, useRef, useState } from 'react'
import type { LogLine, LogLevel } from '../api/types'
import { Button } from './ui'
import { useToast } from './Toast'

/**
 * The job's log, live.
 *
 * Collapsed by default: it is for when something looks wrong, not for
 * watching normally. Auto-scroll stops as soon as you scroll up, because
 * being yanked back to the bottom while reading is worse than missing a
 * line.
 */

const FILTERS: { key: string; label: string; levels: LogLevel[] }[] = [
  { key: 'all', label: 'All', levels: ['debug', 'progress', 'warning', 'error'] },
  { key: 'progress', label: 'Progress', levels: ['progress', 'warning', 'error'] },
  { key: 'problems', label: 'Problems', levels: ['warning', 'error'] },
]

const COLOUR: Record<LogLevel, string> = {
  debug: 'var(--faint)',
  progress: 'var(--text)',
  warning: 'var(--warning)',
  error: 'var(--danger)',
}

export function LogConsole({ lines }: { lines: LogLine[] }) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('progress')
  const [stick, setStick] = useState(true)
  const box = useRef<HTMLDivElement>(null)
  const toast = useToast()

  const levels = FILTERS.find((f) => f.key === filter)?.levels ?? []
  const shown = lines.filter((line) => levels.includes(line.level))

  useEffect(() => {
    if (!open || !stick) return
    const element = box.current
    if (element) element.scrollTop = element.scrollHeight
  }, [shown.length, open, stick])

  async function copy() {
    const text = shown.map((l) => `${l.t} ${l.text}`).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast.show(`${shown.length} lines copied.`, 'success')
    } catch {
      toast.show('The clipboard is not available in this browser.', 'error')
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          data-testid="log-toggle"
        >
          {open ? 'Hide log' : 'Show log'}
          <span className="tnum text-faint">{lines.length}</span>
        </Button>
        {open && (
          <div className="flex items-center gap-1">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                size="sm"
                variant={filter === f.key ? 'secondary' : 'ghost'}
                onClick={() => setFilter(f.key)}
                aria-pressed={filter === f.key}
              >
                {f.label}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={copy}>
              Copy
            </Button>
          </div>
        )}
      </div>

      {open && (
        <div
          ref={box}
          onScroll={(e) => {
            const el = e.currentTarget
            setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 24)
          }}
          className="max-h-64 overflow-y-auto border-t border-border bg-bg px-4 py-2 font-mono text-xs leading-5"
          data-testid="log-console"
        >
          {shown.length === 0 ? (
            <p className="py-2 text-muted">Nothing logged at this level yet.</p>
          ) : (
            shown.map((line, i) => (
              <div key={i} className="flex gap-3">
                <span className="tnum shrink-0 text-faint">{line.t}</span>
                <span
                  className="min-w-0 break-words"
                  style={{ color: COLOUR[line.level] }}
                >
                  {line.text}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
