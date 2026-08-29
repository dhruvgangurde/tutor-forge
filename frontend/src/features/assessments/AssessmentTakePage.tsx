import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAssessmentForStudent, useSubmitAssessment } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import type { StudentQuestion, SubmissionResponseItem } from '../../lib/api/types'
import { getErrorMessage } from '../../lib/api/errors'
import styles from './assessments.module.css'

export function AssessmentTakePage() {
  const { assessmentId } = useParams<{ assessmentId: string }>()
  const navigate = useNavigate()
  // Student-scoped route: the teacher-only GET /assessments/{id} that this
  // used to call returns 403 for a student, which rendered as "not found".
  const { data: assessment, isLoading, error } = useAssessmentForStudent(assessmentId || '')
  const submitMutation = useSubmitAssessment()

  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [responses, setResponses] = useState<Record<string, SubmissionResponseItem>>({})
  const [isReviewing, setIsReviewing] = useState(false)
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false)

  if (!assessmentId) {
    return <div className="text-center py-8">Invalid assessment ID</div>
  }

  if (isLoading) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Loading Assessment</h1>
        </div>
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      </>
    )
  }

  if (error || !assessment) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Assessment Not Found</h1>
        </div>
        <div className="text-center py-8">
          <p className="text-red-600 mb-4">Failed to load assessment.</p>
          <button
            onClick={() => navigate('/assessments')}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Back to Assessments
          </button>
        </div>
      </>
    )
  }

  const questions = assessment.questions || []
  if (questions.length === 0) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Assessment Error</h1>
        </div>
        <div className="text-center py-8">
          <p className="text-red-600 mb-4">This assessment has no questions.</p>
          <button
            onClick={() => navigate('/assessments')}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Back to Assessments
          </button>
        </div>
      </>
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
      <>
        <div className="page-header">
          <h1 className="page-title">Review Your Answers</h1>
          <p className="page-subtitle">{assessment.title}</p>
        </div>

        <div className={styles.reviewContainer}>
          {questions.map((question, idx) => {
            const resp = responses[question.id]
            let answerDisplay = 'Not answered'
            if (resp?.answer_choice) {
              answerDisplay = `Choice: ${resp.answer_choice}`
            } else if (resp?.answer_text) {
              answerDisplay = resp.answer_text.substring(0, 100) + (resp.answer_text.length > 100 ? '...' : '')
            }

            return (
              <div key={question.id} className={styles.reviewItem}>
                <div className={styles.reviewQuestion}>
                  <span className={styles.questionNumber}>Q{idx + 1}</span>
                  <h4>{question.stem}</h4>
                </div>
                <div className={styles.reviewAnswer}>
                  <strong>Your answer:</strong> {answerDisplay}
                </div>
                <button
                  onClick={() => {
                    setCurrentQuestionIndex(idx)
                    setIsReviewing(false)
                  }}
                  className="text-sm text-blue-600 hover:underline"
                >
                  Edit answer
                </button>
              </div>
            )
          })}
        </div>

        <div className={styles.buttonGroup}>
          <button
            onClick={() => setIsReviewing(false)}
            className="px-6 py-2 border border-gray-300 rounded hover:bg-gray-50"
          >
            Back to Questions
          </button>
          <button
            onClick={() => setShowSubmitConfirm(true)}
            disabled={submitMutation.isPending}
            className="px-6 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
          >
            {submitMutation.isPending ? 'Submitting...' : 'Submit Assessment'}
          </button>
        </div>

        {submitMutation.error && (
          <div className="text-center py-4 text-red-600">
            {/* Surface the server's actual detail (same helper the tutor chat
                error path uses) — a hardcoded string hid real causes such as
                a 409 duplicate submission or a 422 validation failure. */}
            <p>
              {getErrorMessage(
                submitMutation.error,
                'Error submitting assessment. Please try again.'
              )}
            </p>
          </div>
        )}

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
      </>
    )
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">{assessment.title}</h1>
        <p className="page-subtitle">
          Question {currentQuestionIndex + 1} of {questions.length}
        </p>
      </div>

      <div className={styles.takingContainer}>
        {/* Question Display */}
        <div className={styles.questionSection}>
          <QuestionDisplay
            question={currentQuestion}
            response={currentResponse}
            onChange={(answer) => handleResponseChange(currentQuestion.id, answer)}
          />
        </div>

        {/* Navigation */}
        <div className={styles.navigationSection}>
          <QuestionNav
            current={currentQuestionIndex}
            total={questions.length}
            onPrevious={handlePrevious}
            onNext={handleNext}
            onJump={handleJumpToQuestion}
          />
        </div>
      </div>
    </>
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
      {options.map((option, idx) => (
        <label key={idx} className={styles.optionLabel}>
          <input
            type="radio"
            name="mcq"
            value={choices[idx]}
            checked={selectedChoice === choices[idx]}
            onChange={(e) => onChange(e.target.value)}
            className={styles.optionInput}
          />
          <span className={styles.optionText}>
            {choices[idx]}. {option}
          </span>
        </label>
      ))}
    </fieldset>
  )
}

function ShortAnswerQuestion({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <fieldset>
      <legend className="sr-only">Enter your answer</legend>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Type your answer here..."
        className={styles.textarea}
        rows={6}
      />
    </fieldset>
  )
}

function NumericQuestion({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <fieldset>
      <legend className="sr-only">Enter your numeric answer</legend>
      <input
        type="number"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Enter a number..."
        className={styles.numberInput}
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
      <div className={styles.progressBar}>
        <div className={styles.progressFill} style={{ width: `${((current + 1) / total) * 100}%` }} />
      </div>

      <div className={styles.questionJump}>
        <span className={styles.jumpLabel}>Jump to question:</span>
        <div className={styles.jumpButtons}>
          {Array.from({ length: total }).map((_, idx) => (
            <button
              key={idx}
              onClick={() => onJump(idx)}
              className={`
                ${styles.jumpButton}
                ${idx === current ? styles.jumpButtonActive : ''}
              `}
              aria-label={`Question ${idx + 1}`}
            >
              {idx + 1}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.buttonGroup}>
        <button
          onClick={onPrevious}
          disabled={current === 0}
          className={`px-4 py-2 border rounded ${current === 0 ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-50'}`}
        >
          Previous
        </button>

        <button
          onClick={onNext}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          {current === total - 1 ? 'Review & Submit' : 'Next'}
        </button>
      </div>
    </div>
  )
}
