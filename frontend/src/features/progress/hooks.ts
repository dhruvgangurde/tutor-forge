import { useQuery } from '@tanstack/react-query'
import { getMyCourseProgress, getMyProgress } from '../../lib/api/progress'
import { queryKeys } from '../../lib/queryKeys'

/** Per-course progress summaries for the signed-in student. */
export function useMyProgress() {
  return useQuery({
    queryKey: queryKeys.progress.mine(),
    queryFn: getMyProgress,
  })
}

/** Concept mastery and released grade history for one course. */
export function useMyCourseProgress(courseId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.progress.course(courseId),
    queryFn: () => getMyCourseProgress(courseId),
    enabled: Boolean(courseId) && enabled,
  })
}
