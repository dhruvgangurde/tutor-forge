import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatInput } from './ChatInput'
import { MAX_TUTOR_QUESTION_CHARS } from '../../lib/limits'

describe('ChatInput length limit', () => {
  it('caps the question at the server limit', () => {
    render(<ChatInput onSendMessage={vi.fn()} />)
    const input = screen.getByPlaceholderText(/Ask a question/)
    expect(input).toHaveAttribute('maxLength', String(MAX_TUTOR_QUESTION_CHARS))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('says so once the limit is reached', () => {
    render(<ChatInput onSendMessage={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(/Ask a question/), {
      target: { value: 'q'.repeat(MAX_TUTOR_QUESTION_CHARS) },
    })
    expect(screen.getByRole('status')).toHaveTextContent('2,000-character limit')
  })

  it('shows a friendly message when the server rejects the length', () => {
    const error = Object.assign(new Error('Request failed with status code 422'), {
      isAxiosError: true,
      response: {
        status: 422,
        data: {
          detail: 'question: String should have at most 2000 characters',
          errors: [{ type: 'string_too_long', ctx: { max_length: 2000 } }],
        },
      },
    })
    render(<ChatInput onSendMessage={vi.fn()} isError error={error} />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'That is too long. Please keep it to 2,000 characters or fewer.'
    )
    expect(screen.getByRole('alert')).not.toHaveTextContent('String should have')
  })
})
