import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAssessmentForStudent, useMySubmissions, useSubmitAssessment } from './hooks'
import { SkeletonBlock } from '../../components/ui/Skeleton'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { NotFoundState } from '../../components/ui/NotFoundState'
import { CheckIcon } from '../../components/ui/icons'
import { GradeStatus } from './GradeStatus'
import type { StudentQuestion, SubmissionResponseItem } from '../../lib/api/types'
import { getErrorMessage } from '../../lib/api/errors'
import { MAX_ANSWER_TEXT_CHARS } from '../../lib/limits'
import styles from './assessments.module.css'

/** Approved quiz footer (design brief): true of the product today. */
function QuizFooter() {
  return <p className={styles.quizFooter}>Your teacher reviews every grade before you see it.</p>
}

export function AssessmentTakePage() {
  const { assessmentId } = useParams<{ assessmentId: string }>()
  const navigate = useNavigate()
  // Student-scoped route: the teacher-only GET /assessments/{id} that this
  // used to call returns 403 for a student, which rendered as "not found".
  const { data: assessment, isLoading, error } = useAssessmentForStudent(assessmentId || '')
  // Each assessment is submitted once. Without this check a submitted quiz
  // opened as a fresh attempt and only failed (409) at the final submit.
  const mySubmissions = useMySubmissions()
  const submitMutation = useSubmitAssessment()

  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [responses, setResponses] = useState<Record<string, SubmissionResponseItem>>({})
  const [isReviewing, setIsReviewing] = useState(false)
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false)

  if (!assessmentId) {
    return <p className="placeholder-label">Invalid assessment ID</p>
  }

  if (isLoading || mySubmissions.isLoading) {
    return (
      <div className={styles.quizColumn}>
        <div className="page-header">
          <h1 className="page-title">Loading Assessment</h1>
        </div>
        <div className={styles.skeletonPanel} aria-hidden="true">
          <SkeletonBlock height="6px" />
          <SkeletonBlock width="80%" height="2rem" />
          <SkeletonBlock height="3.75rem" />
          <SkeletonBlock height="3.75rem" />
          <SkeletonBlock height="3.75rem" />
        </div>
      </div>
    )
  }

  const existing = mySubmissions.data?.find((s) => s.assessment_id === assessmentId)
  if (existing) {
    return (
      <div className={styles.quizColumn}>
        <div className="page-header">
          <h1 className="page-title">{assessment?.title ?? existing.assessment_title}</h1>
          <p className="page-subtitle">{existing.course_name}</p>
        </div>
        <div className={`card ${styles.noticeCard}`} role="status">
          <h2 className={styles.noticeTitle}>You have already submitted this assessment</h2>
          <p className={styles.noticeText}>
            Each assessment can be submitted once. You submitted this one on{' '}
            {new Date(existing.submitted_at).toLocaleString()}.
          </p>
          <GradeStatus
            submission={existing}
            showPercent
            leading={<span className={styles.submittedLabel}>Submitted</span>}
          />
          <div className={styles.noticeActions}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => navigate(`/assessments/submissions/${existing.submission_id}`)}
            >
              View your submission
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => navigate('/assessments')}>
              Back to Assessments
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (error || !assessment) {
    return (
      <NotFoundState
        title="Assessment Not Found"
        message="Failed to load assessment."
        linkTo="/assessments"
        linkLabel="Back to Assessments"
      />
    )
  }

  const questions = assessment.questions || []
  if (questions.length === 0) {
    return (
      <NotFoundState
        title="Assessment Error"
        message="This assessment has no questions."
        linkTo="/assessments"
        linkLabel="Back to Assessments"
      />
    )
  }

  const currentQuestion = questions[currentQuestionIndex]
  const currentResponse = responses[currentQuestion.id]

  const handleResponseChange = (questionId: string, answer: SubmissionResponseItem) => {
    setResponses((prev) => ({
      ...prev,
      [questionId]: answer,
    }))
  }

  const handleNext = () => {
    if (currentQuestionIndex < questions.length - 1) {
      setCurrentQuestionIndex(currentQuestionIndex + 1)
    } else {
      setIsReviewing(true)
    }
  }

  const handlePrevious = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex(currentQuestionIndex - 1)
    }
  }

  const handleJumpToQuestion = (index: number) => {
    setCurrentQuestionIndex(index)
  }

  const handleSubmit = async () => {
    const responseItems = questions.map((q) => {
      const resp = responses[q.id]
      return {
        question_id: q.id,
        answer_text: resp?.answer_text || null,
        answer_choice: resp?.answer_choice || null,
      } as SubmissionResponseItem
    })

    submitMutation.mutate(
      { assessmentId, responses: responseItems },
      {
        onSuccess: (ack) => {
          navigate(`/assessments/submissions/${ack.submission_id}`)
        },
      }
    )
  }

  if (isReviewing) {
    return (
      <div className={styles.quizColumn}>
        <div className="page-header">
          <h1 className="page-title">Review Your Answers</h1>
          <p className="page-subtitle">{assessment.title}</p>
        </div>

        <ol className={styles.reviewList}>
          {questions.map((question, idx) => {
            const resp = responses[question.id]
            let answerDisplay = 'Not answered'
            if (resp?.answer_choice) {
              answerDisplay = `Choice: ${resp.answer_choice}`
            } else if (resp?.answer_text) {
              answerDisplay = resp.answer_text.substring(0, 100) + (resp.answer_text.length > 100 ? '...' : '')
            }

            return (
              <li key={question.id} className={`card ${styles.reviewItem}`}>
                <div className={styles.reviewQuestion}>
                  <span className={styles.questionNumber}>Q{idx + 1}</span>
                  <h4 className={styles.reviewStem}>{question.stem}</h4>
                </div>
                <div className={styles.reviewAnswer}>
                  <strong>Your answer:</strong> {answerDisplay}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setCurrentQuestionIndex(idx)
                    setIsReviewing(false)
                  }}
                  className="btn btn-tertiary btn-sm"
                >
                  Edit answer
                </button>
              </li>
            )
          })}
        </ol>

        {/* Above the buttons, styled, where it is seen. Surfaces the server's
            actual detail (same helper the tutor chat uses) -- a hardcoded
            string hid real causes such as a 409 duplicate submission (e.g.
            submitted from another tab) or a 422 validation failure. */}
        {submitMutation.error && (
          <ErrorBanner
            message={getErrorMessage(
              submitMutation.error,
              'Error submitting assessment. Please try again.'
            )}
          />
        )}

        <div className={styles.quizNavButtons}>
          <button type="button" onClick={() => setIsReviewing(false)} className="btn btn-secondary">
            Back to Questions
          </button>
          <button
            type="button"
            onClick={() => setShowSubmitConfirm(true)}
            disabled={submitMutation.isPending}
            className="btn btn-primary"
          >
            {submitMutation.isPending ? 'Submitting...' : 'Submit Assessment'}
          </button>
        </div>

        <QuizFooter />

        <ConfirmDialog
          open={showSubmitConfirm}
          message="Are you sure you want to submit? You won't be able to change your answers after submission."
          confirmLabel="Submit"
          cancelLabel="Cancel"
          onConfirm={() => {
            setShowSubmitConfirm(false)
            handleSubmit()
          }}
          onCancel={() => setShowSubmitConfirm(false)}
        />
      </div>
    )
  }

  return (
    <div className={styles.quizColumn}>
      {/* The position is shown once: "Question N of M" beside a thin bar. */}
      <div className={styles.quizHeader}>
        <h1 className={styles.quizTitle}>{assessment.title}</h1>
        <div className={styles.progressRow}>
          <div
            className={styles.progressBar}
            role="progressbar"
            aria-label="Quiz progress"
            aria-valuemin={1}
            aria-valuemax={questions.length}
            aria-valuenow={currentQuestionIndex + 1}
          >
            <div
              className={styles.progressFill}
              style={{ width: `${((currentQuestionIndex + 1) / questions.length) * 100}%` }}
            />
          </div>
          <span className={styles.progressText}>
            Question {currentQuestionIndex + 1} of {questions.length}
          </span>
        </div>
      </div>

      <QuestionDisplay
        question={currentQuestion}
        response={currentResponse}
        onChange={(answer) => handleResponseChange(currentQuestion.id, answer)}
      />

      <QuestionNav
        current={currentQuestionIndex}
        total={questions.length}
        onPrevious={handlePrevious}
        onNext={handleNext}
        onJump={handleJumpToQuestion}
      />

      <QuizFooter />
    </div>
  )
}

// ─── Question Display Component ───────────────────────────────────────────

function QuestionDisplay({
  question,
  response,
  onChange,
}: {
  question: StudentQuestion
  response: SubmissionResponseItem | undefined
  onChange: (answer: SubmissionResponseItem) => void
}) {
  const handleChange = (field: 'answer_text' | 'answer_choice', value: string | null) => {
    onChange({
      question_id: question.id,
      [field]: value,
      [field === 'answer_text' ? 'answer_choice' : 'answer_text']: response?.[field === 'answer_text' ? 'answer_choice' : 'answer_text'] || null,
    })
  }

  return (
    <div className={styles.questionContainer}>
      <h2 className={styles.questionStem}>{question.stem}</h2>

      {question.question_type === 'mcq' && question.options && (
        <MCQQuestion
          options={question.options}
          selectedChoice={response?.answer_choice || ''}
          onChange={(choice) => handleChange('answer_choice', choice)}
        />
      )}

      {question.question_type === 'short_answer' && (
        <ShortAnswerQuestion
          value={response?.answer_text || ''}
          onChange={(text) => handleChange('answer_text', text)}
        />
      )}

      {question.question_type === 'numeric' && (
        <NumericQuestion
          value={response?.answer_text || ''}
          onChange={(text) => handleChange('answer_text', text)}
        />
      )}
    </div>
  )
}

function MCQQuestion({
  options,
  selectedChoice,
  onChange,
}: {
  options: string[]
  selectedChoice: string
  onChange: (choice: string) => void
}) {
  const choices = ['A', 'B', 'C', 'D']
  return (
    <fieldset className={styles.optionsContainer}>
      <legend className="sr-only">Choose one answer</legend>
      {options.map((option, idx) => {
        const selected = selectedChoice === choices[idx]
        return (
          <label key={idx} className={`${styles.option} ${selected ? styles.optionSelected : ''}`}>
            <input
              type="radio"
              name="mcq"
              value={choices[idx]}
              checked={selected}
              onChange={(e) => onChange(e.target.value)}
              className={styles.optionInput}
            />
            {/* The letter is drawn in the tile; screen readers get it from the
                hidden prefix, and the checked state from the radio itself. */}
            <span className={styles.optionTile} aria-hidden="true">{choices[idx]}</span>
            <span className={styles.optionText}>
              <span className="sr-only">{choices[idx]}.</span> {option}
            </span>
            {selected && (
              <span className={styles.optionSelectedTag} aria-hidden="true">
                <CheckIcon size={16} />
                Selected
              </span>
            )}
          </label>
        )
      })}
    </fieldset>
  )
}

function ShortAnswerQuestion({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <fieldset className={styles.answerFieldset}>
      <legend className="sr-only">Enter your answer</legend>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Type your answer here..."
        maxLength={MAX_ANSWER_TEXT_CHARS}
        className="field-input"
        rows={6}
      />
    </fieldset>
  )
}

function NumericQuestion({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <fieldset className={styles.answerFieldset}>
      <legend className="sr-only">Enter your numeric answer</legend>
      {/* Text, not type="number": some answers are an ordered list of
          numbers ("3, 27, 38, 43"), and a number input refuses commas. The
          grader parses a single number or a comma-separated list. */}
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Enter a number (separate several values with commas)"
        aria-label="Numeric answer"
        maxLength={MAX_ANSWER_TEXT_CHARS}
        className={`field-input ${styles.numberInput}`}
      />
    </fieldset>
  )
}

function QuestionNav({
  current,
  total,
  onPrevious,
  onNext,
  onJump,
}: {
  current: number
  total: number
  onPrevious: () => void
  onNext: () => void
  onJump: (index: number) => void
}) {
  return (
    <div className={styles.navContainer}>
      <div className={styles.quizNavButtons}>
        <button type="button" onClick={onPrevious} disabled={current === 0} className="btn btn-secondary">
          Previous
        </button>

        <button type="button" onClick={onNext} className="btn btn-primary">
          {current === total - 1 ? 'Review & Submit' : 'Next'}
        </button>
      </div>

      <div className={styles.questionJump}>
        <span className={styles.jumpLabel}>Jump to question:</span>
        <div className={styles.jumpButtons}>
          {Array.from({ length: total }).map((_, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => onJump(idx)}
              className={`${styles.jumpButton} ${idx === current ? styles.jumpButtonActive : ''}`}
              aria-label={`Question ${idx + 1}`}
              aria-current={idx === current ? 'step' : undefined}
            >
              {idx + 1}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
