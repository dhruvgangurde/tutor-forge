import { createContext, useCallback, useContext, useRef, useState, ReactNode } from 'react'
import { ConfirmDialog } from '../components/ui/ConfirmDialog'

interface ConfirmOptions {
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

interface ConfirmContextValue {
  confirm: (message: string, options?: ConfirmOptions) => Promise<boolean>
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null)

interface PendingConfirm extends ConfirmOptions {
  message: string
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null)
  const resolveRef = useRef<((result: boolean) => void) | null>(null)

  const confirm = useCallback((message: string, options?: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve
      setPending({ message, ...options })
    })
  }, [])

  const settle = useCallback((result: boolean) => {
    resolveRef.current?.(result)
    resolveRef.current = null
    setPending(null)
  }, [])

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <ConfirmDialog
        open={pending !== null}
        message={pending?.message ?? ''}
        confirmLabel={pending?.confirmLabel}
        cancelLabel={pending?.cancelLabel}
        danger={pending?.danger}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </ConfirmContext.Provider>
  )
}

/** Replaces window.confirm() app-wide: `if (await confirm('Delete this?')) { ... }` */
export function useConfirm(): ConfirmContextValue['confirm'] {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider')
  return ctx.confirm
}
