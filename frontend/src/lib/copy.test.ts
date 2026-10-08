import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { plural } from './plural'
import { statusLabel } from './statusVariant'

// Copy fixes (frontend audit #10).

describe('plural', () => {
  it('uses the singular only for exactly one', () => {
    expect(plural(1, 'assessment')).toBe('1 assessment')
    expect(plural(0, 'assessment')).toBe('0 assessments')
    expect(plural(2, 'released grade')).toBe('2 released grades')
    expect(plural(1, 'pt', 'pts')).toBe('1 pt')
    expect(plural(2.5, 'pt', 'pts')).toBe('2.5 pts')
  })
})

describe('statusLabel', () => {
  it('never shows a raw enum value', () => {
    for (const raw of ['short_answer', 'mcq', 'pending_review', 'ingesting', 'failed', 'published', 'overridden']) {
      const label = statusLabel(raw)
      expect(label).not.toContain('_')
      expect(label).not.toBe(raw)
    }
    expect(statusLabel('short_answer')).toBe('Short answer')
    expect(statusLabel('mcq')).toBe('Multiple choice')
  })

  it('keeps unknown values readable', () => {
    expect(statusLabel('some_new_state')).toBe('some new state')
  })
})

describe('student chat timestamp', () => {
  it('is light on the indigo student bubble, not the dim grey used elsewhere', () => {
    const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const rule = css.match(/\.chat-bubble\.student \.message-time\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    expect(rule![1]).toMatch(/color:\s*rgba\(255,\s*255,\s*255/)
  })
})
