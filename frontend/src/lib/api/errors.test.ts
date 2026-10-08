import { describe, expect, it } from 'vitest'
import { getErrorMessage, isNotFoundError } from './errors'

function apiError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data },
  })
}

describe('getErrorMessage: over-long input (422)', () => {
  it('turns a text length error into a sentence naming the limit', () => {
    const err = apiError(422, {
      error: 'validation_error',
      detail: 'question: String should have at most 2000 characters',
      errors: [{ type: 'string_too_long', loc: ['body', 'question'], ctx: { max_length: 2000 } }],
    })
    expect(getErrorMessage(err)).toBe('That is too long. Please keep it to 2,000 characters or fewer.')
  })

  it('explains a list that has too many items', () => {
    const err = apiError(422, {
      detail: 'responses: List should have at most 100 items after validation, not 101',
      errors: [{ type: 'too_long', loc: ['body', 'responses'], ctx: { max_length: 100 } }],
    })
    expect(getErrorMessage(err)).toBe('Too many items in one request (the limit is 100).')
  })

  it('handles an email address that is too long', () => {
    const err = apiError(422, {
      detail: 'email: value is not a valid email address: The email address is too long',
      errors: [{ type: 'value_error', msg: 'value is not a valid email address: The email address is too long (1 character too many).' }],
    })
    expect(getErrorMessage(err)).toBe('That is too long. Please shorten it and try again.')
  })

  it('leaves other errors to the server detail', () => {
    expect(getErrorMessage(apiError(400, { detail: 'Course is archived.' }))).toBe('Course is archived.')
  })
})

describe('getErrorMessage: never shows raw backend text', () => {
  const malformedId = apiError(422, {
    error: 'validation_error',
    detail: 'course_id: Input should be a valid UUID, invalid character: found `n` at 1',
    errors: [{ type: 'uuid_parsing', loc: ['path', 'course_id'], msg: 'Input should be a valid UUID' }],
  })

  it('treats a malformed id in the URL as not found', () => {
    expect(getErrorMessage(malformedId)).toBe('This page could not be found. Check the link and try again.')
    expect(getErrorMessage(malformedId)).not.toContain('UUID')
  })

  it('replaces other validation wording with a plain sentence', () => {
    const err = apiError(422, {
      detail: 'responses.0.answer_choice: Value error, answer_choice must be one of A, B, C, D',
      errors: [{ type: 'value_error', loc: ['body', 'responses', 0, 'answer_choice'], msg: 'Value error, bad choice' }],
    })
    expect(getErrorMessage(err)).toBe('Some of the details entered are not valid. Please check them and try again.')
  })

  it('asks for a valid email address on an email error', () => {
    const err = apiError(422, {
      detail: 'email: value is not a valid email address: An email address must have an @-sign.',
      errors: [
        {
          type: 'value_error',
          loc: ['body', 'email'],
          msg: 'value is not a valid email address: An email address must have an @-sign.',
        },
      ],
    })
    expect(getErrorMessage(err)).toBe('Please enter a valid email address.')
  })

  it('hides server errors and bare status lines', () => {
    expect(getErrorMessage(apiError(500, { detail: 'Internal Server Error' }))).toBe(
      'Something went wrong on our side. Please try again in a moment.'
    )
    expect(getErrorMessage(apiError(403, {}), 'Fallback.')).toBe('Fallback.')
  })

  it('explains a network failure', () => {
    const offline = Object.assign(new Error('Network Error'), { isAxiosError: true })
    expect(getErrorMessage(offline)).toBe('Could not reach the server. Check your connection and try again.')
  })
})

describe('isNotFoundError', () => {
  it('is true for a 404 and for a malformed id', () => {
    expect(isNotFoundError(apiError(404, { detail: 'Course not found.' }))).toBe(true)
    expect(
      isNotFoundError(apiError(422, { errors: [{ type: 'uuid_parsing', loc: ['path', 'course_id'] }] }))
    ).toBe(true)
  })

  it('is false for other failures', () => {
    expect(isNotFoundError(apiError(400, { detail: 'Bad.' }))).toBe(false)
    expect(isNotFoundError(apiError(422, { errors: [{ type: 'missing', loc: ['body', 'name'] }] }))).toBe(false)
    expect(isNotFoundError(new Error('x'))).toBe(false)
  })
})
