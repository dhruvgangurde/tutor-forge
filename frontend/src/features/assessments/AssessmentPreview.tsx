import { useState } from 'react'
import { useAssessmentDraft, usePublishAssessment } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { QuestionEditForm } from './QuestionEditForm'
import { mcqKeyIndex } from './answerKey'
import { Badge } from '../../components/ui/Badge'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { AlertIcon } from '../../components/ui/icons'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import { plural } from '../../lib/plural'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import { useConfirm } from '../../hooks/useConfirm'

interface AssessmentPreviewProps {
  assessmentId: string
  courseId: string
}

/**
 * Teacher preview of a generated assessment: questions, the answer key (the
 * option marked correct, or the expected answer for numeric / short answer),
 * rubric criteria, and a publish action while the assessment is still a draft.
 */
export function AssessmentPreview({ assessmentId, courseId }: AssessmentPreviewProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const { data: draft, isLoading, isError, error } = useAssessmentDraft(assessmentId)
  const publish = usePublishAssessment(courseId)
  const { showToast } = useToast()
  const confirm = useConfirm()

  async function handlePublish() {
    const ok = await confirm(
      'Publish this assessment? Students will be able to take it immediately, and this cannot be undone.',
      { confirmLabel: 'Publish' }
    )
    if (!ok) return

    try {
      await publish.mutateAsync(assessmentId)
      showToast('Assessment published.', 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  if (isLoading) return <SkeletonRows label="Loading assessment…" rows={3} columns={1} />
  if (isError) return <ErrorBanner message={getErrorMessage(error)} />
  if (!draft) return null

  if (draft.status === 'generating') {
    return <Spinner label="Generating assessment — this can take a moment…" />
  }

  if (draft.status === 'failed') {
    return (
      <ErrorBanner
        message={draft.generation_error ?? 'Assessment generation failed.'}
      />
    )
  }

  return (
    <div className="assessment-preview">
      <div className="preview-header">
        <h3 className="preview-title">{draft.title}</h3>
        <Badge variant={statusToVariant(draft.status)}>{statusLabel(draft.status)}</Badge>
      </div>

      {/* Every draft, not only one with a broken key: a generated key can be
          wrong while still naming a real option. */}
      {draft.status === 'draft' && (
        <p className="answer-key-notice">
          <AlertIcon size={18} className="answer-key-warning-icon" />
          Check every answer key before you publish.
        </p>
      )}

      <ol className="question-list">
        {draft.questions.map((q, idx) => (
          <li key={q.id} className="question-item">
            <div className="question-stem-row">
              <div className="question-stem">
                {idx + 1}. {q.stem}
              </div>
              {/* Draft only: a published paper must not change underneath
                  students who have already answered it. */}
              {draft.status === 'draft' && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setEditingId(editingId === q.id ? null : q.id)}
                >
                  {editingId === q.id ? 'Cancel' : 'Edit'}
                </button>
              )}
            </div>
            {/* Quiet metadata line, not a chip per attribute. */}
            <p className="question-meta">
              {[
                statusLabel(q.question_type),
                q.bloom_level && statusLabel(q.bloom_level),
                q.difficulty && statusLabel(q.difficulty),
                plural(q.max_points, 'pt', 'pts'),
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>

            {q.options && (
              <ul className="question-options">
                {/* Options are stored as plain text; the letter comes from
                    position, exactly as the student-facing take page does it.
                    Rendering the raw string here would show no letter at all. */}
                {q.options.map((opt, idx) => {
                  // The option the grader will accept. A teacher has to be able
                  // to check this at a glance before publishing: a generated
                  // key can be wrong, and grading follows the key exactly.
                  const isKey = idx === mcqKeyIndex(q.correct_answer, q.options?.length ?? 0)
                  return (
                    <li
                      key={`${idx}-${opt}`}
                      className={isKey ? 'question-option question-option-correct' : 'question-option'}
                    >
                      <span className="question-option-letter">{String.fromCharCode(65 + idx)}.</span>{' '}
                      <span className="question-option-text">{opt}</span>
                      {isKey && <span className="answer-key-tag">✓ Marked correct</span>}
                    </li>
                  )
                })}
              </ul>
            )}

            {q.question_type === 'mcq' &&
              mcqKeyIndex(q.correct_answer, q.options?.length ?? 0) === null && (
                <p className="answer-key-warning">
                  <AlertIcon size={18} className="answer-key-warning-icon" />
                  No valid answer key
                  {q.correct_answer ? ` (stored as “${q.correct_answer}”)` : ''} — students
                  cannot score on this question. Edit it to mark the correct option.
                </p>
              )}

            {q.question_type !== 'mcq' && q.correct_answer && (
              <p className="answer-key-line">
                <span className="answer-key-label">Answer key:</span>{' '}
                <span className={q.question_type === 'numeric' ? 'answer-key-value answer-key-mono' : 'answer-key-value'}>
                  {q.correct_answer}
                </span>
              </p>
            )}

            {q.rubric_criteria.length > 0 && (
              <ul className="rubric-list">
                {q.rubric_criteria.map((rc) => (
                  <li key={rc.id}>
                    {rc.description} — {plural(rc.max_points, 'pt', 'pts')}
                  </li>
                ))}
              </ul>
            )}

            {editingId === q.id && (
              <QuestionEditForm
                assessmentId={assessmentId}
                question={q}
                onDone={() => setEditingId(null)}
              />
            )}
          </li>
        ))}
      </ol>

      {draft.status === 'draft' && (
        <button
          type="button"
          className="btn btn-primary"
          onClick={handlePublish}
          disabled={publish.isPending}
        >
          {publish.isPending ? 'Publishing…' : 'Publish assessment'}
        </button>
      )}

      {draft.status === 'published' && draft.published_at && (
        <p className="page-subtitle">
          Published {new Date(draft.published_at).toLocaleString()}
        </p>
      )}
    </div>
  )
}
