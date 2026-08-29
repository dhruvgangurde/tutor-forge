// Centralized React Query key factory.
// Every feature must build cache keys through this factory so invalidation
// stays consistent across the app (e.g. invalidating queryKeys.courses.all()
// after an upload invalidates every courses list/detail query at once).

export const queryKeys = {
  courses: {
    all: () => ['courses'] as const,
    list: () => ['courses', 'list'] as const,
    available: () => ['courses', 'available'] as const,
    detail: (courseId: string) => ['courses', courseId] as const,
    structure: (courseId: string) => ['courses', courseId, 'structure'] as const,
  },
  tutor: {
    all: () => ['tutor'] as const,
    sessions: () => ['tutor', 'sessions'] as const,
    messages: (sessionId: string) => ['tutor', 'sessions', sessionId, 'messages'] as const,
  },
  assessments: {
    all: () => ['assessments'] as const,
    byCourse: (courseId: string) => ['assessments', 'course', courseId] as const,
    available: (courseId: string) => ['assessments', 'available', courseId] as const,
    detail: (assessmentId: string) => ['assessments', assessmentId] as const,
    take: (assessmentId: string) => ['assessments', assessmentId, 'take'] as const,
  },
  grading: {
    all: () => ['grading'] as const,
    queue: () => ['grading', 'queue'] as const,
    detail: (submissionId: string) => ['grading', submissionId] as const,
    myGrades: (courseId?: string) => ['grading', 'my-grades', courseId || ''] as const,
  },
} as const
