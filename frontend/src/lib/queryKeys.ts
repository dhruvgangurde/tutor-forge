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
    deletionImpact: (courseId: string) =>
      ['courses', courseId, 'deletion-impact'] as const,
    enrollments: (courseId: string) => ['courses', courseId, 'enrollments'] as const,
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
  progress: {
    all: () => ['progress'] as const,
    mine: () => ['progress', 'me'] as const,
    course: (courseId: string) => ['progress', 'me', 'course', courseId] as const,
  },
  grading: {
    all: () => ['grading'] as const,
    queue: () => ['grading', 'queue'] as const,
    // Student view of their own submission: GET /assessments/submissions/{id}.
    detail: (submissionId: string) => ['grading', submissionId] as const,
    // Teacher review of the AI recommendation: GET /grading/{id}.
    // Deliberately a separate key from detail() above: the two endpoints take
    // the same submissionId but return different shapes, so sharing a key would
    // let one view read the other's cached payload.
    review: (submissionId: string) => ['grading', 'review', submissionId] as const,
    myGrades: (courseId?: string) => ['grading', 'my-grades', courseId || ''] as const,
  },
} as const
