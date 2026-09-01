import { Markdown } from '../../components/ui/Markdown'
import { citationLabel } from '../../lib/sourceName'
import type { Citation } from '../../lib/api/types'

interface ChatBubbleProps {
  role: 'student' | 'tutor'
  content: string
  citations: Citation[]
  isRefusal: boolean
  timestamp: string
  /** Hint ladder depth, 0 for a plain chat turn. Drives the rung label. */
  hintLevel?: number
}

/** Mirrors MAX_HINT_LEVEL in backend/agents/tutor/prompts.py. */
const FULL_EXPLANATION_LEVEL = 4

function hintRungLabel(level: number): string {
  return level >= FULL_EXPLANATION_LEVEL ? 'Full explanation' : `Hint ${level}`
}

/**
 * Collapse citations that would render as the same chip.
 *
 * Retrieval returns several chunks and they frequently come from one page, so
 * a live answer produced "Earth, page 4 | Earth, page 4 | Earth, page 1 |
 * Earth, page 1 | Earth, page 2". Once the confidence number is gone the
 * duplicates carry no information at all — they are the same label twice.
 * Chunks arrive ranked, so the first of a group is the best-matching one and
 * its passage is what the tooltip should quote.
 */
function dedupeByLabel(citations: Citation[]): Citation[] {
  const seen = new Set<string>()
  return citations.filter((c) => {
    const key = `${c.source_file}|${c.page_or_slide}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/**
 * Individual chat message bubble with citations.
 *
 * The tutor's text is rendered as markdown: the model emits `###` headings and
 * `**bold**`, and this used to put it in a bare <p>, so students read the raw
 * characters. Student messages stay plain — a student typing `*` means an
 * asterisk, not emphasis.
 *
 * Citations name the source and page but no longer show the retrieval
 * confidence percentage. That number is the groundedness gate's internal
 * cosine score; "earth.pdf p.1 61%" invited students to read it as the tutor
 * being 61% sure of its answer. The teacher-facing grading view
 * (features/grading/CitationList.tsx) still shows it, because there it is
 * genuinely diagnostic.
 */
export function ChatBubble({
  role,
  content,
  citations,
  isRefusal,
  timestamp,
  hintLevel = 0,
}: ChatBubbleProps) {
  const isTutor = role === 'tutor'
  const isHint = isTutor && hintLevel > 0
  const sources = dedupeByLabel(citations)

  return (
    <div className={`chat-bubble-row ${role}`}>
      <div className={`chat-bubble ${role} ${isRefusal ? 'refusal' : ''}`}>
        {isHint && (
          <div className="hint-rung-label">{hintRungLabel(hintLevel)}</div>
        )}

        {isTutor ? <Markdown>{content}</Markdown> : <p>{content}</p>}

        {sources.length > 0 && (
          <div className="citations">
            <span className="citations-label">Sources</span>
            {sources.map((c, idx) => (
              <div
                key={idx}
                className="citation-badge"
                title={`${c.source_file}\n\n${c.chunk_text}`}
              >
                {citationLabel(c.source_file, c.page_or_slide)}
              </div>
            ))}
          </div>
        )}
        <div className="message-time">{new Date(timestamp).toLocaleTimeString()}</div>
      </div>
    </div>
  )
}
