import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  generateAssessment,
  getAssessment,
  getAssessmentForStudent,
  getMySubmissions,
  getPublishedAssessments,
  getSubmissionDetail,
  listCourseAssessments,
  publishAssessment,
  submitAssessment,
} from '../../lib/api/assessments'
import { queryKeys } from '../../lib/queryKeys'
import { POLL_INTERVAL_MS } from '../../lib/constants'
import type {
  AssessmentDraft,
  AssessmentSummary,
  GenerateAssessmentRequest,
  SubmissionResponseItem,
} from '../../lib/api/types'

/** List assessments for a course. Polls while any assessment is still generating. */
export function useCourseAssessments(courseId: string) {
  return useQuery({
    queryKey: queryKeys.assessments.byCourse(courseId),
    queryFn: () => listCourseAssessments(courseId),
    enabled: Boolean(courseId),
    refetchInterval: (query) => {
      const items = query.state.data as AssessmentSummary[] | undefined
      const hasGenerating = items?.some((a) => a.status === 'generating')
      return hasGenerating ? POLL_INTERVAL_MS : false
    },
  })
}

/** Full draft (questions + rubric) for teacher preview. Polls while generating. */
export function useAssessmentDraft(assessmentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.assessments.detail(assessmentId),
    queryFn: () => getAssessment(assessmentId),
    enabled: Boolean(assessmentId) && enabled,
    refetchInterval: (query) => {
      const draft = query.state.data as AssessmentDraft | undefined
      return draft?.status === 'generating' ? POLL_INTERVAL_MS : false
    },
  })
}

/** Kick off assessment generation; invalidates the course's assessment list. */
export function useGenerateAssessment(courseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: GenerateAssessmentRequest) => generateAssessment(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.assessments.byCourse(courseId) })
    },
  })
}

/** Publish a draft assessment; invalidates both the detail and the course list. */
export function usePublishAssessment(courseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (assessmentId: string) => publishAssessment(assessmentId),
    onSuccess: (_data, assessmentId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.assessments.detail(assessmentId) })
      queryClient.invalidateQueries({ queryKey: queryKeys.assessments.byCourse(courseId) })
    },
  })
}

// ─── Student-facing hooks ────────────────────────────────────────────────────

/** List published assessments available for the student to take. */
export function usePublishedAssessments(courseId?: string) {
  return useQuery({
    queryKey: queryKeys.assessments.available(courseId || ''),
    queryFn: () => getPublishedAssessments(courseId),
  })
}

/**
 * Full question content for a student taking a published assessment.
 *
 * Deliberately NOT useAssessmentDraft: that hook calls the teacher-only
 * GET /assessments/{id}, which 403s for a student and left the take page
 * stuck on its "Assessment Not Found" branch. This one calls the
 * student-scoped /take route.
 *
 * No refetchInterval either -- the draft hook polls while status is
 * "generating", but a student can only ever reach a published assessment,
 * and re-fetching underneath someone mid-answer is not wanted.
 */
export function useAssessmentForStudent(assessmentId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.assessments.take(assessmentId),
    queryFn: () => getAssessmentForStudent(assessmentId),
    enabled: Boolean(assessmentId) && enabled,
  })
}

/** List the current student's submissions and their grading status. */
export function useMySubmissions(courseId?: string) {
  return useQuery({
    queryKey: queryKeys.grading.myGrades(courseId),
    queryFn: () => getMySubmissions(courseId),
  })
}

/** Get full detail of a submission including responses and grading. */
export function useSubmissionDetail(submissionId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.grading.detail(submissionId),
    queryFn: () => getSubmissionDetail(submissionId),
    enabled: Boolean(submissionId) && enabled,
  })
}

/** Submit assessment responses; invalidates submission list. */
export function useSubmitAssessment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      assessmentId,
      responses,
    }: {
      assessmentId: string
      responses: SubmissionResponseItem[]
    }) => submitAssessment(assessmentId, responses),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['grading', 'my-grades'] })
    },
  })
}
