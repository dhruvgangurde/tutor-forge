import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createSession,
  getMessages,
  listSessions,
  requestHint,
  sendChat,
} from '../../lib/api/tutor'
import { queryKeys } from '../../lib/queryKeys'

/** List all tutoring sessions for the current student. */
export function useSessions() {
  return useQuery({
    queryKey: queryKeys.tutor.sessions(),
    queryFn: listSessions,
  })
}

/** Get full message history for a tutoring session. */
export function useMessages(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.tutor.messages(sessionId),
    queryFn: () => getMessages(sessionId),
    enabled: Boolean(sessionId),
  })
}

/** Create a new tutoring session on a course. */
export function useCreateSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (courseId: string) => createSession(courseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tutor.sessions() })
    },
  })
}

/** Send a question to the tutor in a session. */
export function useSendChat(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (question: string) => sendChat(sessionId, question),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tutor.messages(sessionId) })
    },
  })
}

/** Request the next hint level in a session. */
export function useRequestHint(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => requestHint(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tutor.messages(sessionId) })
    },
  })
}
