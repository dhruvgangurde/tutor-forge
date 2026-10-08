import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CitationList } from './CitationList'
import { ChatBubble } from '../tutor/ChatBubble'

const EVIDENCE = [
  {
    text: 'Normalization eliminates redundant data.',
    source_file: 'dbms_notes.pdf',
    page_or_slide: 12,
    confidence: 0.61,
  },
]

describe('CitationList (teacher grading view)', () => {
  it('keeps the retrieval confidence', () => {
    // Deliberate divergence from the student chat. A teacher deciding whether
    // to accept a recommendation is exactly who this score is diagnostic for.
    render(<CitationList citations={EVIDENCE} />)
    expect(screen.getByText('61%')).toBeInTheDocument()
  })

  it('hides the percentage when the score is unknown instead of showing 0%', () => {
    // Audit #9: every chip read "0%" because unmatched quotes defaulted to 0.0.
    const { container } = render(
      <CitationList
        citations={[
          { ...EVIDENCE[0], confidence: null },
          { ...EVIDENCE[0], source_file: 'legacy.pdf', confidence: 0 },
        ]}
      />
    )
    expect(container.querySelectorAll('.citation-badge')).toHaveLength(2)
    expect(container.querySelector('.citation-confidence')).toBeNull()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('humanises the source name like the student view does', () => {
    render(<CitationList citations={EVIDENCE} />)
    expect(screen.getByText('Dbms Notes, page 12')).toBeInTheDocument()
  })

  it('keeps the raw filename and the quoted passage in the tooltip', () => {
    const { container } = render(<CitationList citations={EVIDENCE} />)
    const title = container.querySelector('.citation-badge')?.getAttribute('title') ?? ''
    expect(title).toContain('dbms_notes.pdf')
    expect(title).toContain('Normalization eliminates redundant data.')
  })

  it('renders nothing when there is no evidence', () => {
    const { container } = render(<CitationList citations={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('student and teacher citation views diverge', () => {
  it('shows the percentage to a teacher and not to a student', () => {
    // One assertion pinning both halves of the decision, so neither surface
    // can drift into the other's behaviour unnoticed.
    const teacher = render(<CitationList citations={EVIDENCE} />)
    expect(teacher.container.textContent).toContain('61%')

    const student = render(
      <ChatBubble
        role="tutor"
        content="Normalization removes duplicated data."
        isRefusal={false}
        timestamp="2026-09-01T10:00:00Z"
        citations={[
          {
            chunk_text: 'Normalization eliminates redundant data.',
            source_file: 'dbms_notes.pdf',
            page_or_slide: 12,
            confidence: 0.61,
          },
        ]}
      />
    )
    expect(student.container.textContent).not.toContain('61%')
    // Both name the same source, so the student still knows where to look.
    expect(student.container.textContent).toContain('Dbms Notes, page 12')
  })
})
