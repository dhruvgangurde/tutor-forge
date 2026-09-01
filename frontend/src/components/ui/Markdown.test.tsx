import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('renders bold text as an element, not literal asterisks', () => {
    // The reported defect: tutor replies showed raw ** characters.
    const { container } = render(<Markdown>{'Water is **essential** here.'}</Markdown>)
    expect(container.querySelector('strong')).toHaveTextContent('essential')
    expect(container.textContent).not.toContain('**')
  })

  it('renders a heading without showing the hashes', () => {
    const { container } = render(<Markdown>{'### Key idea\n\nSome text.'}</Markdown>)
    expect(container.textContent).not.toContain('###')
    expect(screen.getByText('Key idea')).toBeInTheDocument()
  })

  it('flattens headings so they do not compete with the page title', () => {
    // A model-authored `#` inside a chat bubble must not become a document h1.
    const { container } = render(<Markdown>{'# Big\n\n## Smaller'}</Markdown>)
    expect(container.querySelector('h1')).toBeNull()
    expect(container.querySelector('h2')).toBeNull()
    expect(container.querySelectorAll('.markdown-heading')).toHaveLength(2)
  })

  it('renders bullet lists as real list items', () => {
    const { container } = render(
      <Markdown>{'- photosynthesis\n- respiration'}</Markdown>
    )
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('renders inline code', () => {
    const { container } = render(<Markdown>{'Call `binarySearch()` here.'}</Markdown>)
    expect(container.querySelector('code')).toHaveTextContent('binarySearch()')
  })

  it('renders italics', () => {
    const { container } = render(<Markdown>{'This is *emphasis*.'}</Markdown>)
    expect(container.querySelector('em')).toHaveTextContent('emphasis')
  })

  it('drops disallowed HTML rather than rendering it', () => {
    // Every string this component receives is LLM output. A hallucinated tag
    // must not reach the DOM.
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)"> and <iframe></iframe>'}</Markdown>
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('strips a javascript: link', () => {
    const { container } = render(
      <Markdown>{'[click me](javascript:alert(1))'}</Markdown>
    )
    const anchor = container.querySelector('a')
    // Either the anchor is gone or its href was emptied — never the script URL.
    expect(anchor?.getAttribute('href') ?? '').not.toContain('javascript:')
    expect(container.textContent).toContain('click me')
  })

  it('keeps an ordinary https link and marks it noopener', () => {
    const { container } = render(
      <Markdown>{'[docs](https://example.com/notes)'}</Markdown>
    )
    const anchor = container.querySelector('a')
    expect(anchor).toHaveAttribute('href', 'https://example.com/notes')
    expect(anchor?.getAttribute('rel')).toContain('noopener')
  })

  it('renders a GFM table as a real table', () => {
    // A live DBMS explanation answered with a pipe table. Without remark-gfm
    // this rendered as literal "| EmployeeID | Name |" rows.
    const { container } = render(
      <Markdown>
        {'| EmployeeID | Name |\n|---|---|\n| 1 | John |\n| 2 | Jane |'}
      </Markdown>
    )
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
    expect(container.textContent).not.toContain('|---|')
    // Wide tables scroll inside the bubble instead of widening the page.
    expect(container.querySelector('.markdown-table-wrap')).not.toBeNull()
  })

  it('renders strikethrough', () => {
    const { container } = render(<Markdown>{'This is ~~wrong~~ right.'}</Markdown>)
    expect(container.querySelector('del')).toHaveTextContent('wrong')
  })

  it('renders a task list checkbox as read-only', () => {
    const { container } = render(<Markdown>{'- [x] done\n- [ ] todo'}</Markdown>)
    const boxes = container.querySelectorAll('input[type="checkbox"]')
    expect(boxes).toHaveLength(2)
    // Rendered model output, not a form a student can submit.
    boxes.forEach((b) => expect(b).toBeDisabled())
  })

  it('renders plain text unchanged', () => {
    render(<Markdown>{'Just an ordinary sentence.'}</Markdown>)
    expect(screen.getByText('Just an ordinary sentence.')).toBeInTheDocument()
  })
})
