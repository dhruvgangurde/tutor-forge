import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { plural } from './plural'
import { statusLabel, statusToVariant } from './statusVariant'

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
  it('uses the secondary-text token on the student bubble (4.6:1 on --surface-2)', () => {
    // Restyle phase 1: the bubble moved from indigo to --surface-2, so the
    // white timestamp became --muted. --tertiary would only reach 4.4:1 there.
    const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const rule = css.match(/\.chat-bubble\.student \.message-time\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    expect(rule![1]).toMatch(/color:\s*var\(--muted\)/)
  })
})

describe('statusToVariant (chip meaning)', () => {
  it('keeps red for errors only and amber for anything awaiting review', () => {
    expect(statusToVariant('failed')).toBe('danger')
    expect(statusToVariant('overridden')).toBe('muted')
    expect(statusToVariant('pending_review')).toBe('warning')
    expect(statusToVariant('pending_grading')).toBe('warning')
    expect(statusToVariant('published')).toBe('success')
    expect(statusToVariant('draft')).toBe('muted')
  })
})
