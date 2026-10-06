import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteCourse,
  enrollStudent,
  getCourse,
  getCourseDeletionImpact,
  getCourseStructure,
  listCourses,
  listEnrollments,
  removeEnrollment,
  restoreCourse,
  uploadCourse,
} from '../../lib/api/courses'
import { queryKeys } from '../../lib/queryKeys'
import { POLL_INTERVAL_MS } from '../../lib/constants'
import type { CourseDetail, CourseSummary } from '../../lib/api/types'

const IN_FLIGHT_STATUSES = new Set(['pending', 'ingesting'])

/** List courses owned by the current teacher. Polls while any course is still ingesting. */
export function useCourses() {
  return useQuery({
    queryKey: queryKeys.courses.list(),
    queryFn: listCourses,
    refetchInterval: (query) => {
      const courses = query.state.data as CourseSummary[] | undefined
      const hasInFlight = courses?.some((c) => IN_FLIGHT_STATUSES.has(c.status))
      return hasInFlight ? POLL_INTERVAL_MS : false
    },
  })
}

/** Single course status/detail. Polls while ingestion is pending/in progress. */
export function useCourse(courseId: string) {
  return useQuery({
    queryKey: queryKeys.courses.detail(courseId),
    queryFn: () => getCourse(courseId),
    enabled: Boolean(courseId),
    refetchInterval: (query) => {
      const course = query.state.data as CourseDetail | undefined
      return course && IN_FLIGHT_STATUSES.has(course.status) ? POLL_INTERVAL_MS : false
    },
  })
}

/** Chapter/concept hierarchy — only meaningful once the course is ready. */
export function useCourseStructure(courseId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.courses.structure(courseId),
    queryFn: () => getCourseStructure(courseId),
    enabled: Boolean(courseId) && enabled,
  })
}

/** Upload a new course; invalidates the courses list on success. */
export function useUploadCourse() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ name, files }: { name: string; files: File[] }) => uploadCourse(name, files),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.list() })
    },
  })
}

/**
 * Archive a course, or hard-delete one with no student work.
 *
 * Invalidates the courses list either way. Archiving is reversible and is the
 * default; a hard delete is refused by the backend with 409 when submissions
 * exist, and that message is surfaced verbatim rather than swallowed.
 */
export function useDeleteCourse() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ courseId, hard }: { courseId: string; hard?: boolean }) =>
      deleteCourse(courseId, hard ?? false),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.all() })
    },
  })
}

/** Un-archive a course. Archiving is not a one-way door. */
export function useRestoreCourse() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (courseId: string) => restoreCourse(courseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.all() })
    },
  })
}

/**
 * What a permanent delete would destroy.
 *
 * Fetched only when the teacher opens the delete flow (`enabled`), because a
 * teacher must be able to tell a disposable mis-upload from a course with
 * students' released grades behind it BEFORE confirming.
 */
export function useCourseDeletionImpact(courseId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.courses.deletionImpact(courseId),
    queryFn: () => getCourseDeletionImpact(courseId),
    enabled: Boolean(courseId) && enabled,
  })
}

/** Students enrolled in one of the teacher's courses. */
export function useEnrollments(courseId: string) {
  return useQuery({
    queryKey: queryKeys.courses.enrollments(courseId),
    queryFn: () => listEnrollments(courseId),
    enabled: Boolean(courseId),
  })
}

/** Enroll a student by email; refreshes the roster on success. */
export function useEnrollStudent(courseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (email: string) => enrollStudent(courseId, email),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.enrollments(courseId) })
    },
  })
}

/** Remove a student from the course; refreshes the roster on success. */
export function useRemoveEnrollment(courseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (studentId: string) => removeEnrollment(courseId, studentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.courses.enrollments(courseId) })
    },
  })
}
