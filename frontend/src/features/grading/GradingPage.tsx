import { useState } from 'react'
import { useGradingQueue } from './hooks'
import { GradingQueue } from './GradingQueue'
import { GradingReviewPanel } from './GradingReviewPanel'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'

/**
 * Teacher grading review queue.
 *
 * Every AI recommendation lands here at pending_review and stays there until a
 * teacher approves or overrides it — this page is the human-in-the-loop gate,
 * and it is the only way a FinalGrade ever gets written.
 */
export function GradingPage() {
  const [selectedSubmissionId, setSelectedSubmissionId] = useState<string | null>(null)
  const { data: queue, isLoading, isError, error } = useGradingQueue()

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Grading</h1>
        <p className="page-subtitle">Review AI grade recommendations and finalize grades.</p>
      </div>

      {isLoading && <Spinner label="Loading review queue…" />}
      {isError && <ErrorBanner message={getErrorMessage(error)} />}

      {queue && queue.length === 0 && (
        <EmptyState
          icon="🎓"
          label="Nothing awaiting review. Recommendations appear here once students submit."
        />
      )}

      {queue && queue.length > 0 && (
        <section className="detail-section">
          <h2 className="section-title">Awaiting review ({queue.length})</h2>
          <GradingQueue
            items={queue}
            selectedId={selectedSubmissionId}
            onSelect={setSelectedSubmissionId}
          />
        </section>
      )}

      {selectedSubmissionId && (
        <div className="card">
          {/* Keyed by submission so switching rows remounts the panel and its
              note / override form start empty instead of carrying over. */}
          <GradingReviewPanel
            key={selectedSubmissionId}
            submissionId={selectedSubmissionId}
            onFinalized={() => setSelectedSubmissionId(null)}
          />
        </div>
      )}
    </>
  )
}
