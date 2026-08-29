import type { Citation } from '../../lib/api/types'

interface ChatBubbleProps {
  role: 'student' | 'tutor'
  content: string
  citations: Citation[]
  isRefusal: boolean
  timestamp: string
}

/** Individual chat message bubble with citations. */
export function ChatBubble({
  role,
  content,
  citations,
  isRefusal,
  timestamp,
}: ChatBubbleProps) {
  return (
    <div className={`chat-bubble-row ${role}`}>
      <div className={`chat-bubble ${role} ${isRefusal ? 'refusal' : ''}`}>
        <p>{content}</p>
        {citations.length > 0 && (
          <div className="citations">
            {citations.map((c, idx) => (
              <div key={idx} className="citation-badge" title={c.chunk_text}>
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
        )}
        <div className="message-time">{new Date(timestamp).toLocaleTimeString()}</div>
      </div>
    </div>
  )
}
