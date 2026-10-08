import { isAxiosError } from 'axios'

/**
 * The one place API errors become text a user reads (frontend audit #8).
 *
 * Backend errors are shaped `{ error, detail }` (see backend/core/exceptions.py).
 * A hand-written `detail` (a 400/403/404/409 raised by a route) is meant for
 * people and is shown as is. A request-validation 422 is not: its `detail` is
 * Pydantic's wording ("course_id: Input should be a valid UUID"), so it is
 * mapped to a plain sentence here and never shown raw. Status lines such as
 * "Request failed with status code 500" are never shown either.
 */

interface FieldError {
  type?: string
  loc?: unknown[]
  msg?: string
  ctx?: { max_length?: number }
}

const NOT_FOUND_MESSAGE = 'This page could not be found. Check the link and try again.'
const INVALID_INPUT_MESSAGE = 'Some of the details entered are not valid. Please check them and try again.'
const NETWORK_MESSAGE = 'Could not reach the server. Check your connection and try again.'
const SERVER_MESSAGE = 'Something went wrong on our side. Please try again in a moment.'

function fieldErrors(data: unknown): FieldError[] {
  const errors = (data as { errors?: unknown } | undefined)?.errors
  return Array.isArray(errors) ? (errors as FieldError[]) : []
}

/** A 422 whose every problem is in the URL path, i.e. a malformed id. */
function isMalformedPath(errors: FieldError[]): boolean {
  return errors.length > 0 && errors.every((e) => Array.isArray(e.loc) && e.loc[0] === 'path')
}

/** A readable sentence for a request-validation 422. */
function validationMessage(errors: FieldError[]): string {
  if (isMalformedPath(errors)) return NOT_FOUND_MESSAGE
  for (const err of errors) {
    const max = err.ctx?.max_length
    if (err.type === 'string_too_long') {
      return typeof max === 'number'
        ? `That is too long. Please keep it to ${max.toLocaleString('en-US')} characters or fewer.`
        : 'That is too long. Please shorten it and try again.'
    }
    if (err.type === 'too_long') {
      return typeof max === 'number'
        ? `Too many items in one request (the limit is ${max.toLocaleString('en-US')}).`
        : 'Too many items in one request.'
    }
    if (typeof err.msg === 'string' && /too long/i.test(err.msg)) {
      return 'That is too long. Please shorten it and try again.'
    }
    if (Array.isArray(err.loc) && err.loc.includes('email')) {
      return 'Please enter a valid email address.'
    }
  }
  return INVALID_INPUT_MESSAGE
}

/**
 * True when the thing a page asked for does not exist for this user: a 404,
 * or a malformed id in the URL (a path-validation 422) -- which is the same
 * situation from the user's point of view.
 */
export function isNotFoundError(error: unknown): boolean {
  if (!isAxiosError(error) || !error.response) return false
  const { status, data } = error.response
  return status === 404 || (status === 422 && isMalformedPath(fieldErrors(data)))
}

/** Extract a user-friendly message from an API error. */
export function getErrorMessage(
  error: unknown,
  fallback = 'Something went wrong. Please try again.'
): string {
  if (isAxiosError(error)) {
    const response = error.response
    if (!response) return NETWORK_MESSAGE
    if (response.status === 422) return validationMessage(fieldErrors(response.data))
    if (response.status >= 500) return SERVER_MESSAGE
    const detail = response.data?.detail
    if (typeof detail === 'string' && detail.length > 0) return detail
    return fallback
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}
