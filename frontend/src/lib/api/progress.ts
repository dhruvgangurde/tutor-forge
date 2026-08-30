import api from './client'
import type { CourseProgressDetail, CourseProgressSummary } from './types'

/**
 * The current student's progress across every course they have submitted to.
 * Backend: GET /progress/me (student only, scoped to the caller's own token)
 */
export async function getMyProgress(): Promise<CourseProgressSummary[]> {
  const resp = await api.get<CourseProgressSummary[]>('/progress/me')
  return resp.data
}

/**
 * Concept mastery + released grade history for one course.
 * Backend: GET /progress/me/courses/{course_id} (student only)
 */
export async function getMyCourseProgress(
  courseId: string
): Promise<CourseProgressDetail> {
  const resp = await api.get<CourseProgressDetail>(`/progress/me/courses/${courseId}`)
  return resp.data
}
