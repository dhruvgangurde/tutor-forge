import api from './client'
import type {
  AssessmentDraft,
  AssessmentSummary,
  GenerateAssessmentAck,
  GenerateAssessmentRequest,
  PublishAssessmentAck,
  PublishedAssessmentSummary,
  QuestionUpdateAck,
  QuestionUpdateRequest,
  StudentAssessmentDetail,
  StudentSubmissionDetail,
  StudentSubmissionSummary,
  SubmissionAck,
  SubmissionResponseItem,
} from './types'

/**
 * Kick off grounded assessment generation as a background task.
 * Backend: POST /assessments/generate (teacher only, 202 Accepted)
 */
export async function generateAssessment(
  body: GenerateAssessmentRequest
): Promise<GenerateAssessmentAck> {
  const resp = await api.post<GenerateAssessmentAck>('/assessments/generate', body)
  return resp.data
}

/**
 * List assessment summaries for a course (teacher, owner-scoped).
 * Backend: GET /assessments/course/{course_id}
 */
export async function listCourseAssessments(courseId: string): Promise<AssessmentSummary[]> {
  const resp = await api.get<AssessmentSummary[]>(`/assessments/course/${courseId}`)
  return resp.data
}

/**
 * Get the full draft assessment (questions + rubric) for teacher preview.
 * Backend: GET /assessments/{assessment_id} (teacher only, owner-scoped)
 */
export async function getAssessment(assessmentId: string): Promise<AssessmentDraft> {
  const resp = await api.get<AssessmentDraft>(`/assessments/${assessmentId}`)
  return resp.data
}

/**
 * Get a published assessment's questions so a student can take it.
 * Backend: GET /assessments/{assessment_id}/take (student only, published only)
 *
 * Distinct from getAssessment(): that route is teacher-only and 403s for a
 * student, which is why the take page used to fail to load.
 */
export async function getAssessmentForStudent(
  assessmentId: string
): Promise<StudentAssessmentDetail> {
  const resp = await api.get<StudentAssessmentDetail>(`/assessments/${assessmentId}/take`)
  return resp.data
}

/**
 * Publish a draft assessment so students can take it.
 * Backend: PATCH /assessments/{assessment_id}/publish (teacher only, owner-scoped)
 */
export async function publishAssessment(assessmentId: string): Promise<PublishAssessmentAck> {
  const resp = await api.patch<PublishAssessmentAck>(`/assessments/${assessmentId}/publish`)
  return resp.data
}

/**
 * Submit student responses for a published assessment.
 * Backend: POST /assessments/{assessment_id}/submit (student only)
 */
export async function submitAssessment(
  assessmentId: string,
  responses: SubmissionResponseItem[]
): Promise<SubmissionAck> {
  const resp = await api.post<SubmissionAck>(`/assessments/${assessmentId}/submit`, { responses })
  return resp.data
}

/**
 * List published assessments available for students to take.
 * Backend: GET /assessments/published (student only)
 */
export async function getPublishedAssessments(
  courseId?: string
): Promise<PublishedAssessmentSummary[]> {
  const params = courseId ? { course_id: courseId } : {}
  const resp = await api.get<PublishedAssessmentSummary[]>('/assessments/published', { params })
  return resp.data
}

/**
 * List the current student's submissions.
 * Backend: GET /assessments/my-submissions (student only)
 */
export async function getMySubmissions(courseId?: string): Promise<StudentSubmissionSummary[]> {
  const params = courseId ? { course_id: courseId } : {}
  const resp = await api.get<StudentSubmissionSummary[]>('/assessments/my-submissions', { params })
  return resp.data
}

/**
 * Get the full detail of a student submission including responses and grading.
 * Backend: GET /assessments/submissions/{submission_id} (student only)
 */
export async function getSubmissionDetail(submissionId: string): Promise<StudentSubmissionDetail> {
  const resp = await api.get<StudentSubmissionDetail>(
    `/assessments/submissions/${submissionId}`
  )
  return resp.data
}

/**
 * Edit one question of a DRAFT assessment.
 * Backend: PATCH /assessments/{assessment_id}/questions/{question_id}
 *
 * Partial payload: send only what changed. The backend refuses with 409 if the
 * assessment is already published, since editing it would change the paper
 * underneath students who have already answered.
 */
export async function updateDraftQuestion(
  assessmentId: string,
  questionId: string,
  body: QuestionUpdateRequest
): Promise<QuestionUpdateAck> {
  const resp = await api.patch<QuestionUpdateAck>(
    `/assessments/${assessmentId}/questions/${questionId}`,
    body
  )
  return resp.data
}
