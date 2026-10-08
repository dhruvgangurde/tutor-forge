import { KeyboardEvent, ReactNode, useRef } from 'react'

export interface TabItem {
  id: string
  label: string
}

interface TabsProps {
  /** Accessible name for the tab list, e.g. "Course sections". */
  label: string
  tabs: TabItem[]
  active: string
  onChange: (id: string) => void
  /** Prefix for element ids, so tabs and panels can reference each other. */
  idPrefix: string
}

/**
 * Text tabs with a thin accent underline on the selected one (WAI-ARIA tabs:
 * arrow keys, Home/End, automatic activation). Pair with <TabPanel>.
 */
export function Tabs({ label, tabs, active, onChange, idPrefix }: TabsProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = tabs.length - 1
    const next =
      e.key === 'ArrowRight' ? (index === last ? 0 : index + 1)
      : e.key === 'ArrowLeft' ? (index === 0 ? last : index - 1)
      : e.key === 'Home' ? 0
      : e.key === 'End' ? last
      : null
    if (next === null) return
    e.preventDefault()
    onChange(tabs[next].id)
    refs.current[next]?.focus()
  }

  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, i) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            className={`tab${selected ? ' tab-selected' : ''}`}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({ idPrefix, id, active, children }: {
  idPrefix: string
  id: string
  active: string
  children: ReactNode
}) {
  if (id !== active) return null
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-tab-${id}`}
      tabIndex={0}
      className="tab-panel"
    >
      {children}
    </div>
  )
}
