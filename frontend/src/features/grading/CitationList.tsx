import { citationLabel } from '../../lib/sourceName'
import { DocumentIcon } from '../../components/ui/icons'
import type { EvidenceCitation } from '../../lib/api/types'

interface CitationListProps {
  citations: EvidenceCitation[]
}

/**
 * Evidence badges for a grading recommendation.
 *
 * This is the teacher-facing surface, and it deliberately diverges from the
 * student's chat citations now. The retrieval confidence stays here: a teacher
 * deciding whether to accept a recommendation is exactly who the groundedness
 * score is diagnostic for — a criterion scored off weak evidence is one to look
 * at twice. The student chat drops it (features/tutor/ChatBubble.tsx), because
 * a learner cannot act on it and reads it as confidence in the answer.
 *
 * The source name is humanised the same way in both places; the raw filename is
 * still in the tooltip, alongside the quoted passage.
 */
export function CitationList({ citations }: CitationListProps) {
  if (citations.length === 0) return null

  return (
    <div className="citations">
      {citations.map((c, idx) => {
        // The backend sends null when it could not tie the quote to a
        // retrieved chunk. That is "unknown", not 0%: hide the figure.
        const percent =
          typeof c.confidence === 'number' && c.confidence > 0 ? Math.round(c.confidence * 100) : null
        return (
          <div
            key={idx}
            className="citation-badge"
            title={`${c.source_file}\n\n${c.text}`}
          >
            <DocumentIcon size={14} />
            <span className="citation-file">
              {citationLabel(c.source_file, c.page_or_slide)}
            </span>
            {percent !== null && (
              <span
                className="citation-confidence"
                data-confidence={percent}
                title="Retrieval confidence — how closely this passage matched the answer being graded."
              >
                {percent}%
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
