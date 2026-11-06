import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

type ToastKind = 'info' | 'success' | 'error'

interface Toast {
  id: number
  kind: ToastKind
  message: string
}

const ToastContext = createContext<{
  show: (message: string, kind?: ToastKind) => void
}>({ show: () => {} })

export function useToast() {
  return useContext(ToastContext)
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const show = useCallback((message: string, kind: ToastKind = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, kind, message }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id))
    }, 4500)
  }, [])

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Announced politely: an action's outcome should reach a screen
          reader without stealing focus from what the person is doing. */}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(360px,calc(100vw-32px))] flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="pointer-events-auto rounded-md border border-border bg-surface px-3.5 py-2.5 text-base"
            style={{ boxShadow: 'var(--shadow-pop)' }}
          >
            <span
              className={
                toast.kind === 'error'
                  ? 'text-danger'
                  : toast.kind === 'success'
                    ? 'text-success'
                    : 'text-text'
              }
            >
              {toast.message}
            </span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
