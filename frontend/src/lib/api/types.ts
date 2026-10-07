// Shared TypeScript types mirroring backend Pydantic schemas.
// Field names/shapes must stay in sync with the FastAPI response models —
// see backend/{courses,tutoring,assessments,grading}/schemas.py.

// ── Courses ────────────────────────────────────────────────────────────────

export interface CourseUploadResponse {
  course_id: string
  job_id: string
  status: string
  message: string
}

export interface CourseSummary {
  id: string
  name: string
  status: string
  created_at: string
  archived_at: string | null
  is_archived: boolean
}

export interface CourseDetail {
  id: string
  name: string
  status: string
  created_at: string
  chapter_count: number
  concept_count: number
}

export interface ConceptSummary {
  id: string
  name: string
  description: string | null
  difficulty: string | null
  order_index: number
}

export interface ChapterWithConcepts {
  id: string
  title: string
  order_index: number
  concepts: ConceptSummary[]
}

export interface CourseStructure {
  course_id: string
  name: string
  chapters: ChapterWithConcepts[]
}

// ── Tutor ──────────────────────────────────────────────────────────────────

export interface Citation {
  chunk_text: string
  source_file: string
  page_or_slide: number | null
  confidence: number
}

export interface SessionCreated {
  session_id: string
  course_id: string
}

export interface SessionSummary {
  id: string
  course_id: string
  course_name: string
  /** First question the student asked, truncated. Null for an empty session. */
  title: string | null
  message_count: number
  last_activity_at: string
  current_hint_level: number
  created_at: string
}

export interface TutoringMessageOut {
  id: string
  role: 'student' | 'tutor'
  content: string
  citations: Citation[]
  hint_level: number
  is_refusal: boolean
  created_at: string
}

export interface TutorResponse {
  response: string
  citations: Citation[]
  is_grounded: boolean
  hint_level: number
  session_id: string
}

export interface HintResponse {
  response: string
  citations: Citation[]
  hint_level: number
  session_id: string
}

// ── Assessments ───────────────────────────────────────────────────────────

export type BloomLevel = 'remember' | 'understand' | 'apply' | 'analyze' | 'evaluate' | 'create'
export type QuestionDifficulty = 'easy' | 'medium' | 'hard' | 'mixed'
export type QuestionType = 'mcq' | 'short_answer' | 'numeric'

export interface GenerateAssessmentRequest {
  course_id: string
  title: string
  topic: string
  difficulty?: QuestionDifficulty
  count?: number
  bloom_mix?: Partial<Record<BloomLevel, number>>
  type_mix?: Partial<Record<QuestionType, number>>
}

export interface RubricCriterionDetail {
  id: string
  description: string
  max_points: number
}

export interface QuestionDetail {
  id: string
  question_type: QuestionType
  stem: string
  options: string[] | null
  bloom_level: string | null
  difficulty: string | null
  max_points: number
  rubric_criteria: RubricCriterionDetail[]
  /**
   * Teacher draft only (never in the student take view). MCQ: the letter the
   * grader matches exactly against the student's choice. Numeric / short
   * answer: the expected answer text. Null when no key is stored.
   */
  correct_answer?: string | null
  worked_solution?: string | null
}

export interface AssessmentSummary {
  id: string
  title: string
  status: string
  course_id: string
  question_count: number
  generation_error: string | null
  created_at: string
}

export interface AssessmentDraft {
  id: string
  title: string
  status: string
  course_id: string
  question_count: number
  questions: QuestionDetail[]
  generation_error: string | null
  published_at: string | null
  created_at: string
}

/**
 * A question as served to a student taking an assessment.
 * Backend: StudentQuestion in assessments/schemas.py. Narrower than
 * QuestionDetail on purpose - no answer key, and no authoring metadata
 * (bloom_level / difficulty).
 */
export interface StudentQuestion {
  id: string
  question_type: QuestionType
  stem: string
  options: string[] | null
  max_points: number
  rubric_criteria: RubricCriterionDetail[]
}

/**
 * A published assessment with its questions, student view.
 * Backend: GET /assessments/{id}/take -> StudentAssessmentDetail.
 */
export interface StudentAssessmentDetail {
  id: string
  title: string
  status: string
  course_id: string
  question_count: number
  questions: StudentQuestion[]
  published_at: string | null
}

export interface SubmissionResponseItem {
  question_id: string
  answer_text?: string | null
  answer_choice?: string | null
}

export interface SubmissionAck {
  submission_id: string
  status: string
  message: string
}

export interface PublishedAssessmentSummary {
  id: string
  title: string
  course_id: string
  course_name: string
  question_count: number
  total_points: number
  published_at: string | null
  created_at: string
}

export interface StudentSubmissionSummary {
  submission_id: string
  assessment_id: string
  assessment_title: string
  course_id: string
  course_name: string
  submitted_at: string
  status: string // "pending_grading" | "graded"
  /** null until the teacher finalizes the grade (the API sends null, not undefined). */
  final_score?: number | null
  max_score?: number | null
}

export interface StudentSubmissionResponse {
  question_id: string
  question_text: string
  question_type: QuestionType
  student_answer?: string | null
  student_choice?: string | null
  max_points: number
}

export interface StudentSubmissionDetail {
  submission_id: string
  assessment_id: string
  assessment_title: string
  course_id: string
  course_name: string
  submitted_at: string
  status: string // "pending_grading" | "graded"
  responses: StudentSubmissionResponse[]
  /** null until the teacher finalizes the grade (the API sends null, not undefined). */
  final_score?: number | null
  max_score?: number | null
  graded_at?: string
  feedback?: string
}

export interface GenerateAssessmentAck {
  assessment_id: string
  status: string
  message: string
}

export interface PublishAssessmentAck {
  assessment_id: string
  status: string
}

// ── Grading ───────────────────────────────────────────────────────────────

export interface EvidenceCitation {
  text: string
  source_file: string
  page_or_slide: number | null
  confidence: number
}

export interface CriterionGrade {
  criterion_id: string | null
  description: string
  score: number
  max_points: number
  feedback: string
  citations: EvidenceCitation[]
  /**
   * The groundedness gate could not judge this criterion against course
   * evidence. score is 0 because nothing was evaluated, not because the answer
   * was wrong — the teacher decides.
   */
  requires_review: boolean
}

export interface GradingQueueItem {
  recommendation_id: string
  submission_id: string
  student_email: string | null
  assessment_title: string | null
  recommended_score: number
  max_score: number
  status: string
  submitted_at: string | null
}

/**
 * One question of the assessment and the criteria scored against it.
 * Backend: QuestionGrade in grading/schemas.py.
 *
 * `stem` is hydrated from the questions table at read time — the grading agent
 * does not store it — and is null when the question has since been deleted.
 */
export interface QuestionGrade {
  question_id: string | null
  question_type: string
  stem: string | null
  criteria: CriterionGrade[]
}

export interface GradingDetail {
  recommendation_id: string
  submission_id: string
  student_email: string | null
  assessment_title: string | null
  recommended_score: number
  max_score: number
  status: string
  /** Grouped by question. Was a single flat `criteria` list, which left a
      teacher unable to tell which criterion belonged to which question. */
  questions: QuestionGrade[]
  evidence_citations: EvidenceCitation[]
  created_at: string
}

export interface FinalGradeResponse {
  final_grade_id: string
  final_score: number
  action: 'approved' | 'overridden'
  finalized_at: string
}

// ─── Student progress ────────────────────────────────────────────────────────
// Backend: progress/schemas.py. Every score here comes from a released
// FinalGrade — a submission still awaiting teacher review shows as awaiting,
// never as a score.

export interface CourseProgressSummary {
  course_id: string
  course_name: string
  assessments_graded: number
  assessments_awaiting_grade: number
  earned_points: number
  possible_points: number
  last_graded_at: string | null
}

export interface ConceptMastery {
  concept_id: string
  concept_name: string
  chapter_title: string
  attempts: number
  earned_points: number
  possible_points: number
  /** earned/possible, 0..1 */
  mastery: number
}

export interface AssessmentResult {
  submission_id: string
  assessment_title: string
  final_score: number
  max_score: number
  action: string
  finalized_at: string
  submitted_at: string | null
}

export interface CourseProgressDetail {
  course_id: string
  course_name: string
  earned_points: number
  possible_points: number
  concepts: ConceptMastery[]
  results: AssessmentResult[]
  tutoring_sessions: number
  tutoring_messages: number
  /** Set when there are released grades but no concept breakdown to show. */
  untagged_note: string | null
}

/** What a permanent course delete would destroy. Backend: GET /courses/{id}/deletion-impact */
export interface CourseDeletionImpact {
  course_id: string
  can_hard_delete: boolean
  impact: {
    assessments: number
    submissions: number
    recommendations: number
    final_grades: number
    audit_records: number
    tutoring_sessions: number
    tutoring_messages: number
    concept_mastery_rows: number
  }
  blocking_reason: string | null
}

/** Backend: GET/POST /courses/{course_id}/enrollments (teacher only, owner-scoped) */
export interface Enrollment {
  student_id: string
  email: string
  enrolled_at: string
}

/** Backend: DELETE /courses/{course_id}/enrollments/{student_id} */
export interface EnrollmentRemovedAck {
  course_id: string
  student_id: string
  action: 'removed'
  message: string
}

export interface CourseLifecycleAck {
  course_id: string
  action: 'archived' | 'restored' | 'deleted'
  message: string
}

/** Backend: PATCH /assessments/{id}/questions/{qid} */
export interface QuestionUpdateRequest {
  stem?: string
  options?: string[]
  correct_answer?: string
  worked_solution?: string
  max_points?: number
  rubric_criteria?: { description: string; max_points: number }[]
}

export interface QuestionUpdateAck {
  question_id: string
  assessment_id: string
  updated_fields: string[]
  /** 'unchanged' | 'revalidated' | 'cleared' — what happened to the concept tag. */
  concept_tag: string
}
