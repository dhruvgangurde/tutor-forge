import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { TutoringMessageOut } from '../../lib/api/types'
import { ChatThread } from './ChatThread'

// Fix round 1: tutor chat layout. jsdom does no layout, so these pin the CSS
// rules and the DOM shape the rules rely on; the document height itself was
// checked in a real browser.
const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')

function rule(selector: string): string {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const norm = (s: string) => s.trim().replace(/\s+/g, ' ')
  const found = [...stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)].find((m) => norm(m[1]) === norm(selector))
  expect(found, `no CSS rule for ${selector}`).toBeDefined()
  return found![2]
}

function msg(over: Partial<TutoringMessageOut>): TutoringMessageOut {
  return {
    id: Math.random().toString(36).slice(2),
    role: 'tutor',
    content: 'text',
    citations: [],
    hint_level: 0,
    is_refusal: false,
    created_at: '2026-10-07T10:00:00Z',
    ...over,
  }
}

const LONG_CHAT = Array.from({ length: 30 }, (_, i) =>
  msg({ role: i % 2 ? 'tutor' : 'student', content: `Message ${i}` })
)

describe('tutor chat: hidden labels stay inside the log', () => {
  it('makes the scrolling log the positioned ancestor of every hidden label', () => {
    expect(rule('.chat-thread')).toMatch(/position:\s*relative/)
    const { container } = render(<ChatThread messages={LONG_CHAT} />)
    const hidden = container.querySelectorAll('.sr-only')
    expect(hidden).toHaveLength(15)
    for (const el of hidden) expect(el.closest('.chat-thread')).not.toBeNull()
  })

  it("subtracts the top bar's 1px border from the page height (desktop and mobile)", () => {
    // Without it the document was one pixel taller than the window and scrolled.
    const heights = [...css.matchAll(/\.tutor-page\s*\{([^}]*)\}/g)].flatMap((m) =>
      [...m[1].matchAll(/height:\s*calc\(([^;]*)\);/g)].map((h) => h[1])
    )
    expect(heights).toHaveLength(3)
    for (const h of heights) expect(h).toMatch(/- 1px/)
  })
})

describe('tutor chat: quiet focus ring on the log', () => {
  it('stays focusable and draws a thin inset ring only on :focus-visible', () => {
    const { getByRole } = render(<ChatThread messages={LONG_CHAT} />)
    expect(getByRole('log', { name: 'Conversation' })).toHaveAttribute('tabindex', '0')
    const ring = rule('.chat-thread:focus-visible')
    expect(ring).toMatch(/outline:\s*2px solid var\(--field-border\)/)
    expect(ring).toMatch(/outline-offset:\s*-2px/)
    expect(ring).not.toMatch(/--accent|box-shadow/)
    expect(css).not.toMatch(/\.chat-thread:focus\s*\{/)
  })
})

describe('tutor chat: readable line length', () => {
  it('caps message text at 62ch (about 70 characters)', () => {
    expect(rule('.chat-bubble > p, .chat-bubble > .markdown')).toMatch(/max-width:\s*62ch/)
  })

  it('renders message text as a direct child of the bubble, so the cap applies', () => {
    const { container } = render(
      <ChatThread messages={[msg({ role: 'student', content: 'Why?' }), msg({ content: 'What do you think?' })]} />
    )
    expect(container.querySelector('.chat-bubble.student > p')).toHaveTextContent('Why?')
    expect(container.querySelector('.chat-bubble.tutor > .markdown')).toHaveTextContent('What do you think?')
  })
})

describe('tutor chat: message cards', () => {
  it('puts tutor replies in a --surface card with a --line edge and 12px radius, no shadow', () => {
    const tutor = rule('.chat-bubble.tutor')
    expect(tutor).toMatch(/background:\s*var\(--surface\)/)
    expect(tutor).toMatch(/border:\s*1px solid var\(--line\)/)
    expect(tutor).toMatch(/border-radius:\s*var\(--radius-lg\)/)
    expect(rule(':root')).toMatch(/--radius-lg:\s*12px/)
    expect(tutor).not.toMatch(/shadow|#[0-9a-f]{3,8}\b|rgba?\(/i)
  })

  it('keeps student messages in the --surface-2 bubble', () => {
    expect(rule('.chat-bubble.student')).toMatch(/background:\s*var\(--surface-2\)/)
  })

  it('keeps the TUTOR label, citations and timestamp inside the card', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            content: 'What do you think?',
            citations: [{ chunk_text: 'x', source_file: 'daa_lab_guide.pdf', page_or_slide: 3, confidence: 0.8 }],
          }),
        ]}
      />
    )
    const card = container.querySelector('.chat-bubble.tutor')!
    expect(card.querySelector('.tutor-label')).toHaveTextContent('Tutor')
    expect(card.querySelector('.citation-badge')).toHaveTextContent('Daa Lab Guide, page 3')
    expect(card.querySelector('.message-time')).not.toBeNull()
  })
})
