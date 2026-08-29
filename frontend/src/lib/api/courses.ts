import api from './client'
import type { CourseDetail, CourseStructure, CourseSummary, CourseUploadResponse } from './types'

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
