import type { EvidenceCitation } from '../../lib/api/types'

interface CitationListProps {
  citations: EvidenceCitation[]
}

/**
 * Evidence badges for a grading recommendation.
 *
 * Same `.citations` / `.citation-badge` markup the tutor chat uses
 * (features/tutor/ChatBubble.tsx); the only difference is the field carrying
 * the quoted passage — EvidenceCitation calls it `text`, the tutor's Citation
 * calls it `chunk_text`. Hovering a badge shows that passage.
 */
export function CitationList({ citations }: CitationListProps) {
  if (citations.length === 0) return null

  return (
    <div className="citations">
      {citations.map((c, idx) => (
        <div key={idx} className="citation-badge" title={c.text}>
          <span className="citation-file">{c.source_file}</span>
          {c.page_or_slide !== null && (
            <span className="citation-page">p. {c.page_or_slide}</span>
          )}
          <span
            className="citation-confidence"
            data-confidence={Math.round(c.confidence * 100)}
          >
            {Math.round(c.confidence * 100)}%
          </span>
        </div>
      ))}
    </div>
  )
}
