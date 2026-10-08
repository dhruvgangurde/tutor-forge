import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { CourseDetailPage } from './CourseDetailPage'

let courseState: { data?: unknown; isLoading: boolean; isError: boolean; error: unknown } = {
  isLoading: false,
  isError: false,
  error: null,
}

vi.mock('./hooks', () => ({
  useCourse: () => courseState,
  useCourseStructure: () => ({ data: undefined, isLoading: false, isError: false }),
  useEnrollments: () => ({ data: [], isLoading: false, isError: false }),
  useEnrollStudent: () => ({ isError: false, isPending: false, reset: vi.fn() }),
  useRemoveEnrollment: () => ({ isPending: false }),
}))
vi.mock('../assessments/hooks', () => ({
  useCourseAssessments: () => ({ data: [], isLoading: false, isError: false }),
}))

function apiError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data },
  })
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/courses/:courseId" element={<CourseDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

function expectFriendlyNotFound() {
  expect(screen.getByRole('heading', { name: 'Course not found' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Back to courses' })).toHaveAttribute('href', '/courses')
}

describe('Course detail: not found', () => {
  it('gives a missing course a heading, an explanation and a way back', () => {
    courseState = { isLoading: false, isError: true, error: apiError(404, { detail: 'Course not found.' }) }
    renderAt('/courses/00000000-0000-0000-0000-000000000000')
    expectFriendlyNotFound()
    expect(screen.getByText(/doesn't exist, or it isn't one of yours/)).toBeInTheDocument()
  })

  it('shows the same page for a malformed id, never the validation text', () => {
    courseState = {
      isLoading: false,
      isError: true,
      error: apiError(422, {
        detail: 'course_id: Input should be a valid UUID',
        errors: [{ type: 'uuid_parsing', loc: ['path', 'course_id'], msg: 'Input should be a valid UUID' }],
      }),
    }
    renderAt('/courses/not-a-uuid')
    expectFriendlyNotFound()
    expect(screen.queryByText(/valid UUID/)).not.toBeInTheDocument()
  })

  it('keeps a way back for other failures', () => {
    courseState = { isLoading: false, isError: true, error: apiError(500, { detail: 'boom' }) }
    renderAt('/courses/c1')
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong on our side.')
    expect(screen.getByRole('link', { name: /Back to courses/ })).toBeInTheDocument()
  })
})
