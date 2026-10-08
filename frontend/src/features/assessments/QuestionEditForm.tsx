import { FormEvent, useState } from 'react'
import { useUpdateDraftQuestion } from './hooks'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import type { QuestionDetail, QuestionUpdateRequest } from '../../lib/api/types'
import { MCQ_LETTERS, mcqKeyIndex } from './answerKey'

interface QuestionEditFormProps {
  assessmentId: string
  question: QuestionDetail
  onDone: () => void
}

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
 *
 * The answer key is shown, not hidden: the option currently marked correct is
 * pre-selected among the real option texts, so a teacher can see a wrong
 * generated key and fix it with one click. It is only sent when it changes.
 */
export function QuestionEditForm({
  assessmentId,
  question,
  onDone,
}: QuestionEditFormProps) {
  const isMcq = question.question_type === 'mcq'
  const isShortAnswer = question.question_type === 'short_answer'

  // The key as stored: an option letter for an MCQ (null when it points at no
  // option), the expected answer text otherwise.
  const storedKeyIdx = isMcq ? mcqKeyIndex(question.correct_answer, question.options?.length ?? 0) : null
  const storedKey = isMcq
    ? storedKeyIdx === null ? '' : MCQ_LETTERS[storedKeyIdx]
    : (question.correct_answer ?? '').trim()

  const [stem, setStem] = useState(question.stem)
  const [options, setOptions] = useState<string[]>(question.options ?? [])
  const [correctAnswer, setCorrectAnswer] = useState(storedKey)
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

  function buildPayload(): QuestionUpdateRequest | null {
    const body: QuestionUpdateRequest = {}
    if (stem.trim() && stem !== question.stem) body.stem = stem.trim()

    if (isMcq) {
      const changed =
        options.length === (question.options ?? []).length &&
        options.some((o, i) => o !== (question.options ?? [])[i])
      if (changed) body.options = options.map((o) => o.trim())
    }

    const newKey = correctAnswer.trim()
    if (newKey && newKey !== storedKey) body.correct_answer = newKey

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
          <fieldset className="field-group answer-choices">
            <legend className="field-label">Correct answer</legend>
            {storedKeyIdx === null && (
              <p className="field-error">
                {question.correct_answer
                  ? `The stored key “${question.correct_answer}” does not match any option`
                  : 'No answer key is stored'}{' '}
                — pick the correct option.
              </p>
            )}
            {options.map((opt, idx) => {
              const letter = MCQ_LETTERS[idx]
              const selected = correctAnswer === letter
              return (
                <label
                  key={letter}
                  className={`answer-choice${selected ? ' answer-choice-selected' : ''}`}
                >
                  <input
                    type="radio"
                    name={`correct-${question.id}`}
                    value={letter}
                    checked={selected}
                    onChange={() => setCorrectAnswer(letter)}
                    disabled={update.isPending}
                  />
                  <span>
                    {letter}. {opt || <em>(empty option)</em>}
                  </span>
                  {idx === storedKeyIdx && (
                    <span className="answer-key-tag">Currently marked correct</span>
                  )}
                </label>
              )
            })}
            {correctAnswer && correctAnswer !== storedKey && (
              <span className="field-hint">
                {storedKey
                  ? `Saving changes the correct answer from ${storedKey} to ${correctAnswer}.`
                  : `Saving marks ${correctAnswer} as the correct answer.`}
              </span>
            )}
          </fieldset>
        </>
      )}

      {!isMcq && (
        <div className="field-group">
          <label htmlFor={`key-${question.id}`} className="field-label">
            {isShortAnswer ? 'Answer key (what a full answer should cover)' : 'Answer key'}
          </label>
          <input
            id={`key-${question.id}`}
            type="text"
            className="field-input"
            value={correctAnswer}
            onChange={(e) => setCorrectAnswer(e.target.value)}
            disabled={update.isPending}
          />
        </div>
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
