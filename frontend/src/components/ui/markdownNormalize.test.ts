import { describe, expect, it } from 'vitest'
import { normalizeMarkdown } from './markdownNormalize'

const TABLE = '| EmployeeID | Name |\n|------------|------|\n| 1 | John |'

describe('normalizeMarkdown', () => {
  it('separates a table that starts straight after a bullet', () => {
    // Verbatim shape from a live DBMS explanation. GFM treats these rows as
    // lazy continuation of the list paragraph, so the student saw raw pipes.
    const input = `- **Employees Table**\n${TABLE}`
    expect(normalizeMarkdown(input)).toBe(`- **Employees Table**\n\n${TABLE}`)
  })

  it('separates a table that starts straight after a paragraph', () => {
    const input = `Here is the schema:\n${TABLE}`
    expect(normalizeMarkdown(input)).toBe(`Here is the schema:\n\n${TABLE}`)
  })

  it('leaves an already well-formed table untouched', () => {
    const input = `#### Employees\n\n${TABLE}`
    expect(normalizeMarkdown(input)).toBe(input)
  })

  it('does not insert a blank line inside a table', () => {
    const input = `Text\n${TABLE}`
    // Exactly one blank line is added, at the top of the run.
    expect(normalizeMarkdown(input).split('\n\n')).toHaveLength(2)
  })

  it('leaves table-shaped lines inside a code fence alone', () => {
    // These are code, not a table. Rewriting them would corrupt the sample.
    const input = 'Run this:\n```\n| a | b |\n|---|---|\n```'
    expect(normalizeMarkdown(input)).toBe(input)
  })

  it('ignores a pipe-wrapped line with no delimiter row under it', () => {
    // Not a table — just a sentence that happens to be wrapped in pipes.
    const input = 'Some text\n| not actually a table |'
    expect(normalizeMarkdown(input)).toBe(input)
  })

  it('returns text with no pipes completely unchanged', () => {
    const input = '### Heading\n\nSome **bold** text.\n\n- one\n- two'
    expect(normalizeMarkdown(input)).toBe(input)
  })

  it('handles several tables in one document', () => {
    const input = `- **A**\n${TABLE}\n\n- **B**\n${TABLE}`
    const out = normalizeMarkdown(input)
    expect(out).toBe(`- **A**\n\n${TABLE}\n\n- **B**\n\n${TABLE}`)
  })

  it('does not add a leading blank line for a document opening with a table', () => {
    expect(normalizeMarkdown(TABLE)).toBe(TABLE)
  })

  it('is idempotent', () => {
    const once = normalizeMarkdown(`- **Employees Table**\n${TABLE}`)
    expect(normalizeMarkdown(once)).toBe(once)
  })
})
