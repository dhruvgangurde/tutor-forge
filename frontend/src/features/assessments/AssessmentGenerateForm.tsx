import { FormEvent, useState } from 'react'
import { useGenerateAssessment } from './hooks'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import type { QuestionDifficulty } from '../../lib/api/types'

const DIFFICULTIES: QuestionDifficulty[] = ['mixed', 'easy', 'medium', 'hard']
const MIN_COUNT = 1
const MAX_COUNT = 30

interface AssessmentGenerateFormProps {
  courseId: string
  onDone: (assessmentId: string) => void
}

/**
 * Assessment generation form. Bloom-mix / type-mix customization is
 * intentionally omitted — the backend already applies sensible defaults
 * when they're left unset (see GenerateRequest schema), so exposing them
 * here would add form complexity without a corresponding requirement.
 */
export function AssessmentGenerateForm({ courseId, onDone }: AssessmentGenerateFormProps) {
  const [title, setTitle] = useState('')
  const [topic, setTopic] = useState('')
  const [difficulty, setDifficulty] = useState<QuestionDifficulty>('mixed')
  const [count, setCount] = useState(10)
  const generate = useGenerateAssessment(courseId)
  const { showToast } = useToast()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    try {
      const ack = await generate.mutateAsync({ course_id: courseId, title, topic, difficulty, count })
      showToast('Assessment generation started.', 'success')
      setTitle('')
      setTopic('')
      onDone(ack.assessment_id)
    } catch {
      // Error surfaced via generate.isError below.
    }
  }

  return (
    <form onSubmit={handleSubmit} className="login-form">
      {generate.isError && <ErrorBanner message={getErrorMessage(generate.error)} />}

      <div className="field-group">
        <label htmlFor="assessment-title-input" className="field-label">
          Title
        </label>
        <input
          id="assessment-title-input"
          type="text"
          className="field-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
          placeholder="e.g. Chapter 3 Quiz"
        />
      </div>

      <div className="field-group">
        <label htmlFor="assessment-topic-input" className="field-label">
          Topic
        </label>
        <input
          id="assessment-topic-input"
          type="text"
          className="field-input"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          required
          minLength={3}
          placeholder="The concept or topic to focus questions on"
        />
      </div>

      <div className="field-row">
        <div className="field-group">
          <label htmlFor="assessment-difficulty-select" className="field-label">
            Difficulty
          </label>
          <select
            id="assessment-difficulty-select"
            className="field-input"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as QuestionDifficulty)}
          >
            {DIFFICULTIES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <div className="field-group">
          <label htmlFor="assessment-count-input" className="field-label">
            Question count
          </label>
          <input
            id="assessment-count-input"
            type="number"
            className="field-input"
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            min={MIN_COUNT}
            max={MAX_COUNT}
            required
          />
        </div>
      </div>

      <button type="submit" className="btn btn-primary" disabled={generate.isPending}>
        {generate.isPending ? 'Starting…' : 'Generate assessment'}
      </button>
    </form>
  )
}
