import { Routes, Route } from 'react-router-dom'
import { LoginPage } from './features/auth/LoginPage'
import { SignupPage } from './features/auth/SignupPage'
import { ProtectedRoute } from './features/auth/ProtectedRoute'
import { CoursesPage } from './features/courses/CoursesPage'
import { CourseDetailPage } from './features/courses/CourseDetailPage'
import { SessionsPage } from './features/tutor/SessionsPage'
import { TutorPage } from './features/tutor/TutorPage'
import { AssessmentsPage } from './features/assessments/AssessmentsPage'
import { AssessmentTakePage } from './features/assessments/AssessmentTakePage'
import { MySubmissionsPage } from './features/assessments/MySubmissionsPage'
import { SubmissionDetailPage } from './features/assessments/SubmissionDetailPage'
import { GradingPage } from './features/grading/GradingPage'
import { AppShell } from './components/layout/AppShell'
import { ErrorBoundary } from './components/ErrorBoundary'
import { NotFoundPage } from './pages/NotFoundPage'
import { NotAuthorizedPage } from './pages/NotAuthorizedPage'

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />

      {/* Authenticated shell — sidebar + error boundary wrap every route below.
          ProtectedRoute redirects to /login before AppShell ever renders, so a
          single wildcard nested here safely covers both cases: unauthenticated
          visitors never reach it (redirected first), authenticated visitors on
          an unknown path see NotFoundPage with the sidebar intact. */}
      <Route element={<ProtectedRoute />}>
        <Route
          element={
            <ErrorBoundary>
              <AppShell />
            </ErrorBoundary>
          }
        >
          {/* Teacher-only */}
          <Route element={<ProtectedRoute allowedRole="teacher" />}>
            <Route path="/courses" element={<CoursesPage />} />
            <Route path="/courses/:courseId" element={<CourseDetailPage />} />
            <Route path="/grading" element={<GradingPage />} />
          </Route>

          {/* Student-only */}
          <Route element={<ProtectedRoute allowedRole="student" />}>
            <Route path="/assessments" element={<AssessmentsPage />} />
            <Route path="/assessments/:assessmentId/take" element={<AssessmentTakePage />} />
            <Route path="/assessments/submissions" element={<MySubmissionsPage />} />
            <Route path="/assessments/submissions/:submissionId" element={<SubmissionDetailPage />} />
            <Route path="/tutor" element={<SessionsPage />} />
            <Route path="/tutor/:sessionId" element={<TutorPage />} />
          </Route>

          {/* Authenticated but wrong role lands here (F32) */}
          <Route path="/not-authorized" element={<NotAuthorizedPage />} />

          {/* Unknown authenticated route */}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  )
}
