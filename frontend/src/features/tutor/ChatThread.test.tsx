import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChatThread } from './ChatThread'
import type { TutoringMessageOut } from '../../lib/api/types'

function msg(over: Partial<TutoringMessageOut>): TutoringMessageOut {
  return {
    id: Math.random().toString(36).slice(2),
    role: 'tutor',
    content: 'text',
    citations: [],
    hint_level: 0,
    is_refusal: false,
    created_at: '2026-09-01T10:00:00Z',
    ...over,
  }
}

describe('ChatThread hint markers', () => {
  it('marks a hint so it is not read as a fresh answer', () => {
    // The /hint endpoint persists no student turn, so an escalation used to
    // appear as an unexplained second tutor bubble.
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'How do glaciers form?' }),
          msg({ content: 'What conditions do you think are needed?' }),
          msg({ content: 'Think about snowfall exceeding melt.', hint_level: 1 }),
        ]}
      />
    )
    expect(screen.getByText('You asked for a hint')).toBeInTheDocument()
  })

  it('distinguishes a second rung on the same question', () => {
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'How do glaciers form?' }),
          msg({ content: 'Hint one.', hint_level: 1 }),
          msg({ content: 'Hint two.', hint_level: 2 }),
        ]}
      />
    )
    expect(screen.getByText('You asked for a hint')).toBeInTheDocument()
    expect(
      screen.getByText('You asked for another hint on the same question')
    ).toBeInTheDocument()
  })

  it('labels each bubble with its rung', () => {
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'Q' }),
          msg({ content: 'Hint one.', hint_level: 1 }),
          msg({ content: 'Hint three.', hint_level: 3 }),
        ]}
      />
    )
    expect(screen.getByText('Hint 1')).toBeInTheDocument()
    expect(screen.getByText('Hint 3')).toBeInTheDocument()
  })

  it('names the top rung a full explanation rather than "Hint 4"', () => {
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'Q' }),
          msg({ content: 'Here is the whole thing.', hint_level: 4 }),
        ]}
      />
    )
    expect(screen.getByText('Full explanation')).toBeInTheDocument()
    expect(screen.queryByText('Hint 4')).toBeNull()
  })

  it('adds no marker to an ordinary chat exchange', () => {
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'How do glaciers form?' }),
          msg({ content: 'What conditions do you think are needed?' }),
        ]}
      />
    )
    expect(screen.queryByText(/asked for a hint/)).toBeNull()
    expect(screen.queryByRole('separator')).toBeNull()
  })

  it('treats a new question after a ladder as a fresh start', () => {
    // hint_level resets to 0 on a chat turn, so the next ladder must not be
    // marked as continuing the previous one.
    render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'First question' }),
          msg({ content: 'Hint two.', hint_level: 2 }),
          msg({ role: 'student', content: 'Second question' }),
          msg({ content: 'Answer.', hint_level: 0 }),
          msg({ content: 'Hint one on the new question.', hint_level: 1 }),
        ]}
      />
    )
    expect(screen.getAllByText('You asked for a hint')).toHaveLength(2)
    expect(screen.queryByText('You asked for another hint on the same question')).toBeNull()
  })
})

describe('ChatThread rendering', () => {
  it('renders tutor markdown but leaves student text literal', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({ role: 'student', content: 'Is 2 ** 3 the same as 2^3?' }),
          msg({ content: 'That is **exactly** right.' }),
        ]}
      />
    )
    // The tutor's asterisks became emphasis...
    expect(container.querySelector('strong')).toHaveTextContent('exactly')
    // ...but the student's are their own typing and must survive verbatim.
    expect(screen.getByText('Is 2 ** 3 the same as 2^3?')).toBeInTheDocument()
  })

  it('shows a source citation without a confidence percentage', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            content: 'Earth is about 4.5 billion years old.',
            citations: [
              {
                chunk_text: 'The Earth formed 4.54 billion years ago.',
                source_file: 'earth.pdf',
                page_or_slide: 1,
                confidence: 0.61,
              },
            ],
          }),
        ]}
      />
    )
    expect(screen.getByText('Earth, page 1')).toBeInTheDocument()
    // The exact defect: "earth.pdf p.1 61%" in front of a student.
    expect(container.textContent).not.toContain('61%')
    expect(container.querySelector('.citation-confidence')).toBeNull()
  })

  it('still shows the raw filename in the hover tooltip', () => {
    // The friendly title is for reading; a student going back to their files
    // needs the real name, and so does anyone debugging a citation.
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            content: 'text',
            citations: [
              {
                chunk_text: 'passage',
                source_file: 'earth.pdf',
                page_or_slide: 2,
                confidence: 0.61,
              },
            ],
          }),
        ]}
      />
    )
    expect(container.querySelector('.citation-badge')?.getAttribute('title')).toContain(
      'earth.pdf'
    )
  })

  it('collapses citations that would render as the same chip', () => {
    // A real answer returned five chunks across three pages, so the student
    // saw "Earth, page 4 | Earth, page 4 | Earth, page 1 | Earth, page 1 |
    // Earth, page 2". Without the confidence number the repeats say nothing.
    const cite = (page: number, confidence: number) => ({
      chunk_text: `passage from page ${page} @ ${confidence}`,
      source_file: 'earth.pdf',
      page_or_slide: page,
      confidence,
    })
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            content: 'text',
            citations: [cite(4, 0.74), cite(4, 0.7), cite(1, 0.69), cite(1, 0.66), cite(2, 0.65)],
          }),
        ]}
      />
    )
    const chips = [...container.querySelectorAll('.citation-badge')].map(
      (n) => n.textContent
    )
    expect(chips).toEqual(['Earth, page 4', 'Earth, page 1', 'Earth, page 2'])
    // Chunks arrive ranked, so the surviving chip quotes the best match.
    expect(
      container.querySelector('.citation-badge')?.getAttribute('title')
    ).toContain('@ 0.74')
  })

  it('keeps citations from different files apart', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            content: 'text',
            citations: [
              { chunk_text: 'a', source_file: 'bio_one.txt', page_or_slide: 1, confidence: 0.7 },
              { chunk_text: 'b', source_file: 'bio_two.txt', page_or_slide: 1, confidence: 0.6 },
            ],
          }),
        ]}
      />
    )
    const chips = [...container.querySelectorAll('.citation-badge')].map(
      (n) => n.textContent
    )
    expect(chips).toEqual(['Bio One, page 1', 'Bio Two, page 1'])
  })

  it('shows the empty state with no messages', () => {
    // Restyle phase 3b: the approved empty-chat copy replaced
    // "Start a conversation by asking a question!".
    render(<ChatThread messages={[]} />)
    expect(
      screen.getByRole('heading', { name: 'What would you like to work through today?' })
    ).toBeInTheDocument()
    expect(
      screen.getByText('The tutor asks questions rather than giving answers, and only uses your course material.')
    ).toBeInTheDocument()
  })
})

describe('ChatThread repeated sources', () => {
  // Seen live: every hint on one question retrieved the same passages, so
  // "DAA Complete Lab Guide, page 9 | page 11 | page 2" repeated under each rung.
  const cite = (page: number) => ({
    chunk_text: `passage ${page}`,
    source_file: 'DAA_Complete_Lab_Guide.pdf',
    page_or_slide: page,
    confidence: 0.7,
  })
  const chips = (container: HTMLElement) =>
    [...container.querySelectorAll('.citation-badge')].map((n) => n.textContent)

  it('shows one "Same sources as above" chip when a tutor turn repeats the last one', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({ id: 'a', citations: [cite(9), cite(11)] }),
          msg({ id: 'b', hint_level: 1, citations: [cite(11), cite(9), cite(9)] }),
        ]}
      />
    )
    expect(chips(container)).toEqual([
      'DAA Complete Lab Guide, page 9',
      'DAA Complete Lab Guide, page 11',
      'Same sources as above',
    ])
    // The pages are still there on hover, so nothing is hidden.
    expect(container.querySelector('.same-sources')?.getAttribute('title')).toContain('page 11')
  })

  it('compares with the previous tutor turn, across a student message', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({ id: 'a', citations: [cite(9)] }),
          msg({ id: 's', role: 'student', citations: [] }),
          msg({ id: 'b', citations: [cite(9)] }),
        ]}
      />
    )
    expect(chips(container)).toEqual(['DAA Complete Lab Guide, page 9', 'Same sources as above'])
  })

  it('lists the chips in full when the sources change', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({ id: 'a', citations: [cite(9)] }),
          msg({ id: 'b', citations: [cite(9), cite(11)] }),
        ]}
      />
    )
    expect(chips(container)).not.toContain('Same sources as above')
  })

  it('never collapses a turn with no sources, or after a refusal without any', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({ id: 'a', citations: [cite(9)] }),
          msg({ id: 'r', is_refusal: true, citations: [] }),
          msg({ id: 'b', citations: [cite(9)] }),
        ]}
      />
    )
    expect(chips(container)).toEqual(['DAA Complete Lab Guide, page 9', 'DAA Complete Lab Guide, page 9'])
  })
})
