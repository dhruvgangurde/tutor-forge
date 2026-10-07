import { FormEvent, useState } from 'react'
import { useApproveGrade, useGradingReview, useOverrideGrade } from './hooks'
import { CitationList } from './CitationList'
import { Badge } from '../../components/ui/Badge'
import { Markdown } from '../../components/ui/Markdown'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import { useConfirm } from '../../hooks/useConfirm'

/**
 * Mirrors backend/grading/schemas.py.
 * OverrideRequest.reason is Field(min_length=5, max_length=2000) with a
 * not-blank validator; ApproveRequest.note is max_length=2000.
 * final_score is Field(ge=0.0), and finalize_grade() additionally rejects
 * anything above the recommendation's max_score.
 */
const MIN_REASON_LENGTH = 5
const MAX_REASON_LENGTH = 2000
const MAX_NOTE_LENGTH = 2000

interface GradingReviewPanelProps {
  submissionId: string
  /** Called after a successful approve/override so the parent can clear selection. */
  onFinalized: () => void
}

/**
 * Teacher review of one AI grading recommendation.
 *
 * Language here follows PROJECT-BRIEF §5.6 (also noted in grading/schemas.py):
 * this screen shows a *recommendation* and *suggested feedback*, never an
 * "official grade". Nothing is released to the student until the teacher
 * approves or overrides, and both of those are irreversible — the backend
 * writes a FinalGrade plus a GradeAuditRecord in one transaction and refuses a
 * second one — so both are confirmed before they fire.
 */
export function GradingReviewPanel({ submissionId, onFinalized }: GradingReviewPanelProps) {
  const { data, isLoading, isError, error } = useGradingReview(submissionId)
  const approve = useApproveGrade()
  const override = useOverrideGrade()
  const { showToast } = useToast()
  const confirm = useConfirm()

  const [note, setNote] = useState('')
  const [showOverride, setShowOverride] = useState(false)
  const [scoreInput, setScoreInput] = useState('')
  const [reason, setReason] = useState('')
  const [overrideError, setOverrideError] = useState<string | null>(null)

  const isBusy = approve.isPending || override.isPending

  async function handleApprove() {
    if (!data) return
    const ok = await confirm(
      `Approve the recommended score of ${data.recommended_score} / ${data.max_score}? ` +
        'This finalizes the grade and cannot be undone.',
      { confirmLabel: 'Approve' }
    )
    if (!ok) return

    try {
      await approve.mutateAsync({
        submissionId,
        note: note.trim() ? note.trim() : undefined,
      })
      showToast('Grade approved and finalized.', 'success')
      onFinalized()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleOverride(e: FormEvent) {
    e.preventDefault()
    if (!data) return

    // Validate here as well as on the server so the teacher gets the message
    // next to the field rather than as a 422 toast.
    const parsed = Number(scoreInput)
    if (scoreInput.trim() === '' || Number.isNaN(parsed)) {
      setOverrideError('Enter a score.')
      return
    }
    if (parsed < 0 || parsed > data.max_score) {
      setOverrideError(`Score must be between 0 and ${data.max_score}.`)
      return
    }
    if (reason.trim().length < MIN_REASON_LENGTH) {
      setOverrideError(`Reason must be at least ${MIN_REASON_LENGTH} characters.`)
      return
    }
    setOverrideError(null)

    const ok = await confirm(
      `Override the AI recommendation with a score of ${parsed} / ${data.max_score}? ` +
        'This finalizes the grade and cannot be undone.',
      { confirmLabel: 'Override', danger: true }
    )
    if (!ok) return

    try {
      await override.mutateAsync({ submissionId, finalScore: parsed, reason: reason.trim() })
      showToast('Grade overridden and finalized.', 'success')
      onFinalized()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  if (isLoading) return <Spinner label="Loading recommendation…" />
  if (isError) return <ErrorBanner message={getErrorMessage(error)} />
  if (!data) return null

  return (
    <div className="grading-review">
      <div className="card-header-row">
        <h3 className="card-title">{data.assessment_title ?? 'Submission'}</h3>
        <Badge variant={statusToVariant(data.status)}>{statusLabel(data.status)}</Badge>
      </div>

      <p className="card-meta">
        {data.student_email ?? 'Unknown student'} · Recommended{' '}
        {new Date(data.created_at).toLocaleString()}
      </p>

      <div className="score-summary">
        <div className="score-summary-block">
          <span className="score-summary-label">AI recommended score</span>
          <span className="score-summary-value">
            {data.recommended_score} <span className="score-summary-max">/ {data.max_score}</span>
          </span>
        </div>
        <p className="score-summary-note">
          Instructor review required — this score is not released to the student until you
          approve or override it.
        </p>
      </div>

      <h4 className="section-title">Per-question breakdown</h4>
      {data.questions.length === 0 ? (
        <p className="card-meta">No per-question detail was recorded for this recommendation.</p>
      ) : (
        <ol className="question-list">
          {data.questions.map((q, qIdx) => {
            const earned = q.criteria.reduce((sum, c) => sum + c.score, 0)
            const possible = q.criteria.reduce((sum, c) => sum + c.max_points, 0)
            return (
              <li
                key={q.question_id ?? `question-${qIdx}`}
                className="question-item"
              >
                <div className="question-stem">
                  {qIdx + 1}. {q.stem ?? <em>Question no longer available</em>}
                </div>
                <div className="question-meta">
                  <Badge variant="info">{q.question_type}</Badge>
                  <span className="question-points">
                    {earned} / {possible} pts
                  </span>
                </div>

                {q.criteria.length === 0 ? (
                  <p className="criterion-feedback">No criteria were scored for this question.</p>
                ) : (
                  <ul className="criterion-list">
                    {q.criteria.map((c, cIdx) => (
                      <li
                        key={c.criterion_id ?? `criterion-${qIdx}-${cIdx}`}
                        className="criterion-item"
                      >
                        <div className="criterion-header">
                          <span className="criterion-description">{c.description}</span>
                          {/* A gated criterion scored 0 because nothing could be
                              judged, not because the answer was wrong. Saying so
                              is the difference between the teacher rubber-stamping
                              a zero and actually reading the answer. */}
                          {c.requires_review ? (
                            <Badge variant="warning">Needs review — not scored</Badge>
                          ) : (
                            <Badge variant={c.score >= c.max_points ? 'success' : 'muted'}>
                              {c.score} / {c.max_points} pts
                            </Badge>
                          )}
                        </div>
                        <div className="criterion-feedback">
                          <span className="criterion-feedback-label">Suggested feedback:</span>{' '}
                          {/* Model output, so it arrives with markdown in it. */}
                          <Markdown className="criterion-feedback-body">
                            {c.feedback}
                          </Markdown>
                        </div>
                        <CitationList citations={c.citations} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ol>
      )}

      {data.evidence_citations.length > 0 && (
        <>
          <h4 className="section-title">All evidence cited</h4>
          <CitationList citations={data.evidence_citations} />
        </>
      )}

      <h4 className="section-title">Finalize</h4>

      <div className="field-group">
        <label htmlFor="grading-note-input" className="field-label">
          Note for the audit record (optional)
        </label>
        <textarea
          id="grading-note-input"
          className="field-input"
          rows={2}
          maxLength={MAX_NOTE_LENGTH}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Agrees with my own read of question 2."
          disabled={isBusy}
        />
      </div>

      <div className="review-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={isBusy}
        >
          {approve.isPending ? 'Approving…' : 'Approve recommended score'}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => {
            setShowOverride((v) => !v)
            setOverrideError(null)
          }}
          disabled={isBusy}
        >
          {showOverride ? 'Cancel override' : 'Override score'}
        </button>
      </div>

      {showOverride && (
        <form onSubmit={handleOverride} className="override-form">
          {overrideError && <p className="field-error">{overrideError}</p>}

          <div className="field-group">
            <label htmlFor="grading-score-input" className="field-label">
              Final score (0–{data.max_score})
            </label>
            <input
              id="grading-score-input"
              type="number"
              className="field-input"
              min={0}
              max={data.max_score}
              step="0.5"
              value={scoreInput}
              onChange={(e) => setScoreInput(e.target.value)}
              disabled={isBusy}
              required
            />
          </div>

          <div className="field-group">
            <label htmlFor="grading-reason-input" className="field-label">
              Reason for overriding
            </label>
            <textarea
              id="grading-reason-input"
              className="field-input"
              rows={3}
              minLength={MIN_REASON_LENGTH}
              maxLength={MAX_REASON_LENGTH}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Recorded in the audit trail alongside the AI's score."
              disabled={isBusy}
              required
            />
            <span className="field-hint">
              At least {MIN_REASON_LENGTH} characters — stored in the grade audit record.
            </span>
          </div>

          <button type="submit" className="btn btn-danger" disabled={isBusy}>
            {override.isPending ? 'Overriding…' : 'Override and finalize'}
          </button>
        </form>
      )}
    </div>
  )
}
