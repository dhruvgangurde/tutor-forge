import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  approveGrade,
  getGradingDetail,
  getGradingQueue,
  overrideGrade,
} from '../../lib/api/grading'
import { queryKeys } from '../../lib/queryKeys'

/** Submissions with an AI recommendation awaiting this teacher's review. */
export function useGradingQueue() {
  return useQuery({
    queryKey: queryKeys.grading.queue(),
    queryFn: getGradingQueue,
  })
}

/**
 * Full AI recommendation for one submission (teacher review view).
 *
 * Uses queryKeys.grading.review(), not .detail(): .detail() belongs to the
 * student-facing submission view, which hits a different endpoint and returns a
 * different shape for the same submissionId.
 */
export function useGradingReview(submissionId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.grading.review(submissionId),
    queryFn: () => getGradingDetail(submissionId),
    enabled: Boolean(submissionId) && enabled,
  })
}

/**
 * Invalidate what a finalize action changes.
 *
 * The submission leaves the pending_review queue and its recommendation's
 * status changes, so both are dropped. The student's own grade view is
 * deliberately not touched: /grading is teacher-only and a user has one role,
 * so that cache never exists in the browser doing the approving.
 */
function useInvalidateAfterFinalize() {
  const queryClient = useQueryClient()
  return (submissionId: string) => {
    queryClient.invalidateQueries({ queryKey: queryKeys.grading.queue() })
    queryClient.invalidateQueries({ queryKey: queryKeys.grading.review(submissionId) })
  }
}

/** Approve the AI-recommended score as-is, with an optional audit note. */
export function useApproveGrade() {
  const invalidate = useInvalidateAfterFinalize()
  return useMutation({
    mutationFn: ({ submissionId, note }: { submissionId: string; note?: string }) =>
      approveGrade(submissionId, note),
    onSuccess: (_data, { submissionId }) => invalidate(submissionId),
  })
}

/** Replace the AI recommendation with a teacher score + required reason. */
export function useOverrideGrade() {
  const invalidate = useInvalidateAfterFinalize()
  return useMutation({
    mutationFn: ({
      submissionId,
      finalScore,
      reason,
    }: {
      submissionId: string
      finalScore: number
      reason: string
    }) => overrideGrade(submissionId, finalScore, reason),
    onSuccess: (_data, { submissionId }) => invalidate(submissionId),
  })
}
