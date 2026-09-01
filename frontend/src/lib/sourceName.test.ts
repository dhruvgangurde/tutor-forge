import { describe, expect, it } from 'vitest'
import { citationLabel, sourceDisplayName } from './sourceName'

describe('sourceDisplayName', () => {
  it('drops the extension from a plain filename', () => {
    // The reported defect: students saw "earth.pdf" in a citation.
    expect(sourceDisplayName('earth.pdf')).toBe('Earth')
  })

  it('turns underscores into spaced words', () => {
    expect(sourceDisplayName('bio_one.txt')).toBe('Bio One')
  })

  it('handles mixed separators', () => {
    expect(sourceDisplayName('chapter-3_photosynthesis.pdf')).toBe(
      'Chapter 3 Photosynthesis'
    )
  })

  it('keeps deliberate casing like acronyms', () => {
    // Re-casing "DNA" to "Dna" would be worse than leaving the filename alone.
    expect(sourceDisplayName('DNA_replication.pdf')).toBe('DNA Replication')
  })

  it('lowercases joining words that are not first', () => {
    expect(sourceDisplayName('the_origin_of_species.pdf')).toBe(
      'The Origin of Species'
    )
  })

  it('strips a directory prefix rather than making it a word', () => {
    expect(sourceDisplayName('uploads/2026/earth.pdf')).toBe('Earth')
    expect(sourceDisplayName('uploads\\earth.pdf')).toBe('Earth')
  })

  it('leaves an unknown extension alone', () => {
    // Only known document extensions are stripped, so a version number in the
    // name is not mistaken for one.
    expect(sourceDisplayName('notes_v1.2')).toBe('Notes V1 2')
  })

  it('falls back to a label for an empty source', () => {
    // Some older grading rows carry source_file: "". A blank chip reads as a
    // rendering bug.
    expect(sourceDisplayName('')).toBe('Course material')
    expect(sourceDisplayName('   ')).toBe('Course material')
    expect(sourceDisplayName('.pdf')).toBe('Course material')
  })
})

describe('citationLabel', () => {
  it('includes the page when there is one', () => {
    expect(citationLabel('earth.pdf', 1)).toBe('Earth, page 1')
  })

  it('omits the page for a source without one', () => {
    expect(citationLabel('bio_one.txt', null)).toBe('Bio One')
  })

  it('never contains a confidence percentage', () => {
    // The whole point of the change: "earth.pdf p.1 61%" showed students an
    // internal retrieval score they cannot act on.
    expect(citationLabel('earth.pdf', 1)).not.toMatch(/%/)
  })
})
