import api from './client'
import type {
  HintResponse,
  SessionCreated,
  SessionSummary,
  TutorResponse,
  TutoringMessageOut,
} from './types'

/**
 * List tutoring sessions for the current student.
 * Backend: GET /tutor/sessions (student only)
 */
export async function listSessions(): Promise<SessionSummary[]> {
  const resp = await api.get<SessionSummary[]>('/tutor/sessions')
  return resp.data
}

/**
 * Create a new tutoring session on a course.
 * Backend: POST /tutor/sessions (student only)
 */
export async function createSession(courseId: string): Promise<SessionCreated> {
  const resp = await api.post<SessionCreated>('/tutor/sessions', { course_id: courseId })
  return resp.data
}

/**
 * Fetch full conversation history for a session.
 * Backend: GET /tutor/sessions/{session_id}/messages (student only, ownership-checked)
 */
export async function getMessages(sessionId: string): Promise<TutoringMessageOut[]> {
  const resp = await api.get<TutoringMessageOut[]>(`/tutor/sessions/${sessionId}/messages`)
  return resp.data
}

/**
 * Send a question to the Socratic tutor.
 * Backend: POST /tutor/sessions/{session_id}/chat (student only, ownership-checked)
 */
export async function sendChat(sessionId: string, question: string): Promise<TutorResponse> {
  const resp = await api.post<TutorResponse>(`/tutor/sessions/${sessionId}/chat`, { question })
  return resp.data
}

/**
 * Request the next hint level for the current question.
 * Backend: POST /tutor/sessions/{session_id}/hint (student only, ownership-checked)
 */
export async function requestHint(sessionId: string): Promise<HintResponse> {
  const resp = await api.post<HintResponse>(`/tutor/sessions/${sessionId}/hint`)
  return resp.data
}
