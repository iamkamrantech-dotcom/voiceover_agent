import {
  forwardRef, useEffect, useRef, useState, type ReactNode,
} from 'react'
import type { JobState } from '../api/types'

/* ---------------------------------------------------------------- panel */

export function Panel({
  title,
  action,
  children,
  className = '',
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={`rounded-lg border border-border bg-surface ${className}`}
      style={{ boxShadow: 'var(--shadow-panel)' }}
    >
      {title && (
        <header className="flex items-center justify-between gap-4 border-b border-border px-4 py-3">
          <h2 className="text-[14px] font-semibold">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

/* --------------------------------------------------------------- button */

type ButtonProps = {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  children: ReactNode
} & React.ButtonHTMLAttributes<HTMLButtonElement>

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = 'secondary', size = 'md', className = '', children, ...rest },
    ref,
  ) {
    const base =
      'inline-flex items-center justify-center gap-2 rounded-md font-medium ' +
      'transition-colors disabled:opacity-45 disabled:pointer-events-none ' +
      'border whitespace-nowrap'
    const sizes = size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-9 px-3.5 text-base'
    const variants: Record<string, string> = {
      primary:
        'bg-accent text-accent-ink border-transparent hover:bg-accent-hover',
      secondary:
        'bg-surface text-text border-border hover:bg-raised hover:border-border-strong',
      ghost:
        'bg-transparent text-muted border-transparent hover:bg-raised hover:text-text',
      danger:
        'bg-transparent text-danger border-border hover:bg-danger-wash hover:border-danger',
    }
    return (
      <button
        ref={ref}
        className={`${base} ${sizes} ${variants[variant]} ${className}`}
        {...rest}
      >
        {children}
      </button>
    )
  },
)

/* ---------------------------------------------------------------- badge */

const STATE_STYLE: Record<JobState, string> = {
  done: 'text-success bg-success-wash',
  running: 'text-accent bg-accent-wash',
  queued: 'text-muted bg-raised',
  interrupted: 'text-warning bg-warning-wash',
  failed: 'text-danger bg-danger-wash',
  cancelled: 'text-muted bg-raised',
}

const STATE_LABEL: Record<JobState, string> = {
  done: 'Done',
  running: 'Running',
  queued: 'Queued',
  interrupted: 'Interrupted',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function StatusBadge({ state }: { state: JobState }) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-sm px-1.5 text-xs font-medium ${STATE_STYLE[state]}`}
    >
      {STATE_LABEL[state]}
    </span>
  )
}

/* ------------------------------------------------------------- skeleton */

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  )
}

/* ---------------------------------------------------- empty / error state */

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <p className="text-md font-semibold">{title}</p>
      <p className="max-w-sm text-base text-muted">{body}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <p className="text-md font-semibold text-danger">Something went wrong</p>
      <p className="max-w-sm text-base text-muted">{message}</p>
      {onRetry && (
        <Button className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- field */

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string
  hint?: string
  htmlFor?: string
  children: ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-muted">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-faint">{hint}</p>}
    </div>
  )
}

export const inputClass =
  'w-full rounded-md border border-border bg-bg px-3 py-2 text-base ' +
  'placeholder:text-faint focus:border-accent focus:outline-none ' +
  'focus-visible:outline-2 focus-visible:outline-accent ' +
  // A control that cannot be used has to look like it. Settings lock while
  // a job runs, and an untouchable field that looks editable reads as a
  // broken page rather than a deliberate state.
  'disabled:cursor-not-allowed disabled:bg-raised disabled:text-muted ' +
  'disabled:border-border'

/* ----------------------------------------------------------- checkbox */

export function Checkbox({
  label,
  description,
  checked,
  onChange,
  id,
  disabled = false,
}: {
  label: string
  description?: string
  checked: boolean
  onChange: (value: boolean) => void
  id: string
  disabled?: boolean
}) {
  return (
    <div className="flex items-start gap-2.5">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
      />
      <label
        htmlFor={id}
        className={disabled
          ? 'cursor-not-allowed select-none text-muted'
          : 'cursor-pointer select-none'}
      >
        <span className="block text-base">{label}</span>
        {description && (
          <span className="block text-xs text-faint">{description}</span>
        )}
      </label>
    </div>
  )
}

/* -------------------------------------------------------------- dialog */

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  destructive = false,
  busy = false,
}: {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  destructive?: boolean
  busy?: boolean
}) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    confirmRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgb(0 0 0 / 0.45)' }}
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className="w-full max-w-sm rounded-lg border border-border bg-surface p-5"
        style={{ boxShadow: 'var(--shadow-pop)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title" className="text-md font-semibold">
          {title}
        </h2>
        <div className="mt-1.5 text-base text-muted">
          {typeof body === 'string' ? <p>{body}</p> : body}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onCancel}>Cancel</Button>
          <Button
            ref={confirmRef}
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- drawer */

/**
 * A side panel. Over the page below 1280px, beside it above - the block
 * review needs to sit next to the timeline on a desktop and cannot on a
 * phone.
 */
export function Drawer({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div
        className="absolute inset-0"
        style={{ background: 'rgb(0 0 0 / 0.45)' }}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative flex h-full w-full max-w-[460px] flex-col border-l border-border bg-surface"
        style={{ boxShadow: 'var(--shadow-pop)' }}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3">
          <h2 className="truncate text-[14px] font-semibold">{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            Close
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && (
          <footer className="shrink-0 border-t border-border p-4">
            {footer}
          </footer>
        )}
      </aside>
    </div>
  )
}

/* -------------------------------------------------------------- popover */

export function Popover({
  label,
  title,
  children,
}: {
  label: string
  title: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="relative" ref={box}>
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}
              aria-expanded={open}>
        {label}
      </Button>
      {open && (
        <div
          role="dialog"
          aria-label={title}
          className="absolute right-0 z-30 mt-1 w-64 rounded-md border border-border bg-surface p-3"
          style={{ boxShadow: 'var(--shadow-pop)' }}
        >
          <h3 className="mb-2 text-xs font-semibold text-muted">{title}</h3>
          {children}
        </div>
      )}
    </div>
  )
}
