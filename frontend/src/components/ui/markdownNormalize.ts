/**
 * Small repairs to model-authored markdown before it is parsed.
 *
 * The model does not always emit well-formed markdown, and GFM is strict about
 * block boundaries. This is deliberately not a parser and not a general
 * "clean up the text" pass — every rule here fixes a shape observed in real
 * output from this app, and each is narrow enough that it cannot change the
 * meaning of markdown that was already correct.
 */

/** A line that looks like a table row: starts and ends with a pipe. */
function isTableRow(line: string): boolean {
  const t = line.trim()
  return t.startsWith('|') && t.endsWith('|') && t.length > 2
}

/**
 * A line that opens or closes a fenced code block (``` or ~~~).
 *
 * Table-shaped lines inside a fence are code, not a table, and must be left
 * exactly as written.
 */
function isFence(line: string): boolean {
  const t = line.trimStart()
  return t.startsWith('```') || t.startsWith('~~~')
}

/**
 * Put a blank line in front of a table that starts immediately after text.
 *
 * GFM requires a table to begin its own block. A live DBMS explanation wrote:
 *
 *     - **Employees Table**
 *     | EmployeeID | Name |
 *     |------------|------|
 *
 * With no blank line those rows are lazy continuation of the list item's
 * paragraph, so the reader gets a wall of literal pipes instead of a table.
 * The one table in that answer that *did* follow a blank-line-separated
 * heading parsed correctly, which is what identified the rule.
 *
 * Only inserts before the FIRST row of a run, never inside a table, and never
 * inside a fenced code block.
 */
export function normalizeMarkdown(source: string): string {
  if (!source.includes('|')) return source

  const lines = source.split('\n')
  const out: string[] = []
  let inFence = false

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (isFence(line)) {
      inFence = !inFence
      out.push(line)
      continue
    }

    if (!inFence && isTableRow(line)) {
      const previous = out[out.length - 1]
      const startsARun = previous !== undefined && !isTableRow(previous)
      // A table needs a delimiter row under its header to be a table at all;
      // without this check a single stray pipe-wrapped line would be spaced
      // away from the paragraph it belongs to.
      const hasDelimiter = isTableRow(lines[i + 1] ?? '') && /^[|\s:-]+$/.test(lines[i + 1] ?? '')
      if (startsARun && previous.trim() !== '' && hasDelimiter) {
        out.push('')
      }
    }

    out.push(line)
  }

  return out.join('\n')
}
