import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getCourse, getCourseStructure, listCourses, uploadCourse } from '../../lib/api/courses'
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
