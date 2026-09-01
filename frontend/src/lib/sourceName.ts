/**
 * Turning an uploaded filename into something readable in a citation.
 *
 * Why not a stored display name
 * -----------------------------
 * There is no per-file entity to hang one on. A course is 1:N files, but the
 * files become chunks at ingestion and the only per-file identity that survives
 * is `source_file` in the Chroma chunk metadata — the original upload filename.
 *
 * Citing the course name instead was the alternative, and it is strictly worse:
 * the course is already named at the top of the page the citation appears on,
 * so it would tell the student nothing new, and it would lose *which* of
 * several uploaded documents an answer came from — exactly the thing a student
 * going back to their notes needs.
 *
 * Adding a display-name column would only help if teachers could type a name
 * per file at upload, which is a new field, a new table, and a migration for a
 * gain over the filename they already chose. Deriving the title keeps the
 * teacher's own naming and needs no schema change.
 */

/** Extensions we strip. Anything else is left alone, so "Q1.2 Notes" survives. */
const KNOWN_EXTENSIONS = /\.(pdf|txt|md|docx?|pptx?|rtf|html?|csv)$/i

/** Words that read badly title-cased, when they are not the first word. */
const LOWERCASE_WORDS = new Set([
  'a',
  'an',
  'and',
  'as',
  'at',
  'but',
  'by',
  'for',
  'in',
  'of',
  'on',
  'or',
  'the',
  'to',
  'vs',
  'with',
])

/**
 * True when a word already carries deliberate casing we must not overwrite —
 * "DNA", "pH", "McGraw". Re-casing these is worse than leaving them.
 */
function hasIntentionalCasing(word: string): boolean {
  return /[A-Z]/.test(word.slice(1)) || word.toUpperCase() === word
}

function titleCaseWord(word: string, isFirst: boolean): string {
  if (!word) return word
  if (hasIntentionalCasing(word)) return word
  const lower = word.toLowerCase()
  if (!isFirst && LOWERCASE_WORDS.has(lower)) return lower
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

/**
 * A human-readable title for an uploaded source file.
 *
 *   "earth.pdf"                     -> "Earth"
 *   "bio_one.txt"                   -> "Bio One"
 *   "chapter-3_photosynthesis.pdf"  -> "Chapter 3 Photosynthesis"
 *   "DNA_replication.pdf"           -> "DNA Replication"
 *
 * Returns "Course material" for an empty filename rather than an empty badge:
 * source_file is `""` in a few older grading rows, and a blank chip looks like
 * a rendering bug.
 */
export function sourceDisplayName(sourceFile: string): string {
  const raw = (sourceFile ?? '').trim()
  if (!raw) return 'Course material'

  // Keep only the basename: a path separator would otherwise become a word.
  const base = raw.split(/[\\/]/).pop() ?? raw
  const withoutExt = base.replace(KNOWN_EXTENSIONS, '')

  const words = withoutExt
    .replace(/[_\-.]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean)

  if (words.length === 0) return 'Course material'
  return words.map((w, i) => titleCaseWord(w, i === 0)).join(' ')
}

/**
 * The full citation label a student reads: title plus page when there is one.
 *
 * The retrieval confidence score is deliberately absent. It is an internal
 * cosine-similarity signal used by the groundedness gate; a student shown
 * "61%" cannot act on it, and it reads as a confidence in the *answer*, which
 * it is not.
 */
export function citationLabel(
  sourceFile: string,
  pageOrSlide: number | null
): string {
  const name = sourceDisplayName(sourceFile)
  return pageOrSlide !== null && pageOrSlide !== undefined
    ? `${name}, page ${pageOrSlide}`
    : name
}
