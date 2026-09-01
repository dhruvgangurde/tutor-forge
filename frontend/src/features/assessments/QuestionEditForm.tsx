import { FormEvent, useState } from 'react'
import { useUpdateDraftQuestion } from './hooks'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import type { QuestionDetail, QuestionUpdateRequest } from '../../lib/api/types'

interface QuestionEditFormProps {
  assessmentId: string
  question: QuestionDetail
  onDone: () => void
}

const MCQ_LETTERS = ['A', 'B', 'C', 'D']

/**
 * Edit one question of a draft assessment.
 *
 * Sends only what changed: the backend takes a partial payload, so an untouched
 * rubric is never round-tripped and cannot be corrupted by a field the teacher
 * did not open.
 *
 * Options are edited as plain text. The letter is display-only, from position —
 * typing "A." into the box is what produced "A. A. Two billion years ago" in
 * the first place, and the backend strips it defensively either way.
 */
export function QuestionEditForm({
  assessmentId,
  question,
  onDone,
}: QuestionEditFormProps) {
  const [stem, setStem] = useState(question.stem)
  const [options, setOptions] = useState<string[]>(question.options ?? [])
  const [correctAnswer, setCorrectAnswer] = useState('')
  const [maxPoints, setMaxPoints] = useState(String(question.max_points))
  const [rubric, setRubric] = useState(
    question.rubric_criteria.map((c) => ({
      description: c.description,
      max_points: c.max_points,
    }))
  )
  const [formError, setFormError] = useState<string | null>(null)

  const update = useUpdateDraftQuestion(assessmentId)
  const { showToast } = useToast()

  const isMcq = question.question_type === 'mcq'
  const isShortAnswer = question.question_type === 'short_answer'

  function buildPayload(): QuestionUpdateRequest | null {
    const body: QuestionUpdateRequest = {}
    if (stem.trim() && stem !== question.stem) body.stem = stem.trim()

    if (isMcq) {
      const changed =
        options.length === (question.options ?? []).length &&
        options.some((o, i) => o !== (question.options ?? [])[i])
      if (changed) body.options = options.map((o) => o.trim())
      if (correctAnswer) body.correct_answer = correctAnswer
    }

    const points = Number(maxPoints)
    if (maxPoints.trim() && !Number.isNaN(points) && points !== question.max_points) {
      body.max_points = points
    }

    if (isShortAnswer) {
      const original = question.rubric_criteria
      const changed =
        rubric.length !== original.length ||
        rubric.some(
          (c, i) =>
            c.description !== original[i]?.description ||
            c.max_points !== original[i]?.max_points
        )
      if (changed) body.rubric_criteria = rubric
    }

    return Object.keys(body).length > 0 ? body : null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)

    if (isMcq && options.some((o) => !o.trim())) {
      setFormError('Every option needs text.')
      return
    }
    if (isShortAnswer && rubric.some((c) => !c.description.trim() || c.max_points <= 0)) {
      setFormError('Each rubric criterion needs a description and points above zero.')
      return
    }

    const body = buildPayload()
    if (!body) {
      setFormError('Nothing has changed yet.')
      return
    }

    try {
      const ack = await update.mutateAsync({ questionId: question.id, body })
      // The concept tag matters to the teacher: a rewritten question may no
      // longer be about the concept it was auto-tagged to, and that affects
      // the student progress view.
      const tagNote =
        ack.concept_tag === 'cleared'
          ? ' Its concept tag was cleared — the wording no longer matches.'
          : ack.concept_tag === 'revalidated'
            ? ' Concept tag re-checked and still valid.'
            : ''
      showToast(`Question updated (${ack.updated_fields.join(', ')}).${tagNote}`, 'success')
      onDone()
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  return (
    <form onSubmit={handleSubmit} className="question-edit-form">
      {formError && <p className="field-error">{formError}</p>}
      {update.isError && <ErrorBanner message={getErrorMessage(update.error)} />}

      <div className="field-group">
        <label htmlFor={`stem-${question.id}`} className="field-label">
          Question
        </label>
        <textarea
          id={`stem-${question.id}`}
          className="field-input"
          rows={3}
          value={stem}
          onChange={(e) => setStem(e.target.value)}
          disabled={update.isPending}
        />
      </div>

      {isMcq && (
        <>
          {options.map((opt, idx) => (
            <div className="field-group" key={idx}>
              <label htmlFor={`opt-${question.id}-${idx}`} className="field-label">
                Option {MCQ_LETTERS[idx]}
              </label>
              <input
                id={`opt-${question.id}-${idx}`}
                type="text"
                className="field-input"
                value={opt}
                onChange={(e) =>
                  setOptions((prev) =>
                    prev.map((o, i) => (i === idx ? e.target.value : o))
                  )
                }
                disabled={update.isPending}
              />
            </div>
          ))}
          <div className="field-group">
            <label htmlFor={`correct-${question.id}`} className="field-label">
              Correct answer
            </label>
            <select
              id={`correct-${question.id}`}
              className="field-input"
              value={correctAnswer}
              onChange={(e) => setCorrectAnswer(e.target.value)}
              disabled={update.isPending}
            >
              <option value="">Leave unchanged</option>
              {MCQ_LETTERS.slice(0, options.length).map((letter) => (
                <option key={letter} value={letter}>
                  {letter}
                </option>
              ))}
            </select>
            <span className="field-hint">
              The answer key is not shown here — pick a letter only if you are
              changing it.
            </span>
          </div>
        </>
      )}

      {isShortAnswer && (
        <>
          {rubric.map((criterion, idx) => (
            <div className="field-group" key={idx}>
              <label htmlFor={`rub-${question.id}-${idx}`} className="field-label">
                Rubric criterion {idx + 1}
              </label>
              <input
                id={`rub-${question.id}-${idx}`}
                type="text"
                className="field-input"
                value={criterion.description}
                onChange={(e) =>
                  setRubric((prev) =>
                    prev.map((c, i) =>
                      i === idx ? { ...c, description: e.target.value } : c
                    )
                  )
                }
                disabled={update.isPending}
              />
              <input
                type="number"
                className="field-input"
                min={0.5}
                step={0.5}
                value={criterion.max_points}
                onChange={(e) =>
                  setRubric((prev) =>
                    prev.map((c, i) =>
                      i === idx ? { ...c, max_points: Number(e.target.value) } : c
                    )
                  )
                }
                disabled={update.isPending}
              />
            </div>
          ))}
          <span className="field-hint">
            Points follow the rubric total unless you set them explicitly below.
          </span>
        </>
      )}

      <div className="field-group">
        <label htmlFor={`points-${question.id}`} className="field-label">
          Points
        </label>
        <input
          id={`points-${question.id}`}
          type="number"
          className="field-input"
          min={0.5}
          step={0.5}
          value={maxPoints}
          onChange={(e) => setMaxPoints(e.target.value)}
          disabled={update.isPending}
        />
      </div>

      <div className="review-actions">
        <button type="submit" className="btn btn-primary" disabled={update.isPending}>
          {update.isPending ? 'Saving…' : 'Save changes'}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onDone}
          disabled={update.isPending}
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
