import { useEffect, type RefObject } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Keep Tab / Shift+Tab inside a modal while it is open, and hand focus back to
 * whatever had it before the modal opened once it closes.
 *
 * It does not choose the initial focus: a dialog that already focuses its own
 * safe default (ConfirmDialog focuses Cancel) keeps doing so. If nothing inside
 * has focus, the container itself takes it (it needs tabIndex={-1}).
 *
 * Call it before any effect that moves focus into the dialog, so the element
 * captured as "before" is the trigger, not the dialog's own button.
 */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return
    const previous = document.activeElement as HTMLElement | null

    // Run after sibling effects have placed their own initial focus.
    const raf = requestAnimationFrame(() => {
      const el = ref.current
      if (el && !el.contains(document.activeElement)) el.focus()
    })

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Tab') return
      const el = ref.current
      if (!el) return
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) {
        e.preventDefault()
        el.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const current = document.activeElement
      if (e.shiftKey && (current === first || !el.contains(current))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (current === last || !el.contains(current))) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)

    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', handleKeyDown)
      if (previous && previous.isConnected) previous.focus()
    }
  }, [ref, active])
}
