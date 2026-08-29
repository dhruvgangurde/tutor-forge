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
  final_score?: number
  max_score?: number
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
  final_score?: number
  max_score?: number
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

export interface GradingDetail {
  recommendation_id: string
  submission_id: string
  student_email: string | null
  assessment_title: string | null
  recommended_score: number
  max_score: number
  status: string
  criteria: CriterionGrade[]
  evidence_citations: EvidenceCitation[]
  created_at: string
}

export interface FinalGradeResponse {
  final_grade_id: string
  final_score: number
  action: 'approved' | 'overridden'
  finalized_at: string
}
