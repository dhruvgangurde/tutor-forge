import api from './client'
import type {
  CourseDeletionImpact,
  CourseDetail,
  CourseLifecycleAck,
  CourseStructure,
  CourseSummary,
  CourseUploadResponse,
} from './types'

/**
 * Upload a new course with one or more files (PDF/PPTX/TXT).
 * Backend: POST /courses/upload (teacher only)
 */
export async function uploadCourse(name: string, files: File[]): Promise<CourseUploadResponse> {
  const formData = new FormData()
  formData.append('name', name)
  files.forEach((file) => formData.append('files', file))

  const resp = await api.post<CourseUploadResponse>('/courses/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return resp.data
}

/**
 * List courses owned by the current teacher.
 * Backend: GET /courses (teacher only)
 */
export async function listCourses(): Promise<CourseSummary[]> {
  const resp = await api.get<CourseSummary[]>('/courses')
  return resp.data
}

/**
 * Get a single course's status/detail.
 * Backend: GET /courses/{course_id} (teacher only, owner-scoped)
 */
export async function getCourse(courseId: string): Promise<CourseDetail> {
  const resp = await api.get<CourseDetail>(`/courses/${courseId}`)
  return resp.data
}

/**
 * Get the full chapter/concept hierarchy for a course.
 * Backend: GET /courses/{course_id}/structure (teacher only, owner-scoped)
 */
export async function getCourseStructure(courseId: string): Promise<CourseStructure> {
  const resp = await api.get<CourseStructure>(`/courses/${courseId}/structure`)
  return resp.data
}

/**
 * List all ready (fully ingested) courses available for tutoring sessions.
 * Backend: GET /courses/available (student only)
 */
export async function getAvailableCourses(): Promise<CourseSummary[]> {
  const resp = await api.get<CourseSummary[]>('/courses/available')
  return resp.data
}

/**
 * Archive a course (default) or permanently delete one with no student work.
 * Backend: DELETE /courses/{course_id}[?hard=true] (teacher only, owner-scoped)
 *
 * Archiving is always safe and reversible. A hard delete is refused with 409
 * when the course has submissions — released grades are education records.
 */
export async function deleteCourse(
  courseId: string,
  hard = false
): Promise<CourseLifecycleAck> {
  const resp = await api.delete<CourseLifecycleAck>(`/courses/${courseId}`, {
    params: hard ? { hard: true } : undefined,
  })
  return resp.data
}

/**
 * Un-archive a course, making it available to students again.
 * Backend: POST /courses/{course_id}/restore (teacher only, owner-scoped)
 */
export async function restoreCourse(courseId: string): Promise<CourseLifecycleAck> {
  const resp = await api.post<CourseLifecycleAck>(`/courses/${courseId}/restore`)
  return resp.data
}

/**
 * What a permanent delete would destroy — used to warn before asking.
 * Backend: GET /courses/{course_id}/deletion-impact (teacher only)
 */
export async function getCourseDeletionImpact(
  courseId: string
): Promise<CourseDeletionImpact> {
  const resp = await api.get<CourseDeletionImpact>(`/courses/${courseId}/deletion-impact`)
  return resp.data
}
