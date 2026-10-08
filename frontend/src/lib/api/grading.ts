import api from './client'
import type { FinalGradeResponse, FinalizedGradeItem, GradingDetail, GradingQueueItem } from './types'

/**
 * List submissions with AI recommendations awaiting teacher review.
 * Backend: GET /grading/queue (teacher only, owner-scoped)
 */
export async function getGradingQueue(): Promise<GradingQueueItem[]> {
  const resp = await api.get<GradingQueueItem[]>('/grading/queue')
  return resp.data
}

/**
 * Released grades in the teacher's courses, newest first.
 * Backend: GET /grading/finalized (teacher only, owner-scoped)
 */
export async function getFinalizedGrades(): Promise<FinalizedGradeItem[]> {
  const resp = await api.get<FinalizedGradeItem[]>('/grading/finalized')
  return resp.data
}

/**
 * Get the full AI grading recommendation for a submission.
 * Backend: GET /grading/{submission_id} (teacher only, owner-scoped)
 */
export async function getGradingDetail(submissionId: string): Promise<GradingDetail> {
  const resp = await api.get<GradingDetail>(`/grading/${submissionId}`)
  return resp.data
}

/**
 * Approve the AI-recommended score as-is.
 * Backend: POST /grading/{submission_id}/approve (teacher only, owner-scoped)
 */
export async function approveGrade(
  submissionId: string,
  note?: string
): Promise<FinalGradeResponse> {
  const resp = await api.post<FinalGradeResponse>(`/grading/${submissionId}/approve`, { note })
  return resp.data
}

/**
 * Override the AI recommendation with a teacher-supplied score and reason.
 * Backend: POST /grading/{submission_id}/override (teacher only, owner-scoped)
 */
export async function overrideGrade(
  submissionId: string,
  finalScore: number,
  reason: string
): Promise<FinalGradeResponse> {
  const resp = await api.post<FinalGradeResponse>(`/grading/${submissionId}/override`, {
    final_score: finalScore,
    reason,
  })
  return resp.data
}
