import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import api from '../../lib/api/client'
import { getErrorMessage } from '../../lib/api/errors'
import { useAuth } from '../../store/AuthContext'
import styles from './assessments.module.css'

interface SignInAgainDialogProps {
  /** The same student is signed in again: the page retries the submit once. */
  onSignedIn: () => void
  /** Cancel or Escape: the dialog closes and the answers stay on the page. */
  onCancel: () => void
}

/**
 * Sign in again without leaving the quiz, after the submit found the sign-in
 * had expired. The answers only live in this page, so a reload to /login lost
 * all of them.
 *
 * Only the student who started the quiz can continue: their email is fixed,
 * and /auth/me must return the same user id. A different account's new token
 * is revoked again straight away and never stored. The password is only ever
 * sent to /auth/login; it is never logged or shown, and is cleared after use.
 * The existing session handling does the rest (login() also revokes the old,
 * expired token).
 */
export function SignInAgainDialog({ onSignedIn, onCancel }: SignInAgainDialogProps) {
  const { user, login } = useAuth()
  const email = user?.email ?? ''
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const dialogRef = useRef<HTMLDivElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const bodyId = useId()
  const emailId = useId()
  const passwordId = useId()

  // Tab stays inside the dialog (called first, so the trap records the
  // element that had focus before the dialog opened).
  useFocusTrap(dialogRef, true)

  // The password is the only thing to fill in, so it takes focus once, on open.
  useEffect(() => {
    passwordRef.current?.focus()
  }, [])

  // Escape is Cancel. The latest handler is read through a ref so a parent
  // re-render does not re-run the effect above or re-register this one.
  const onCancelRef = useRef(onCancel)
  onCancelRef.current = onCancel
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancelRef.current()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setError(null)
    setBusy(true)
    try {
      const resp = await api.post('/auth/login', { email, password })
      const { access_token, role } = resp.data
      const me = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${access_token}` },
      })
      setPassword('')
      if (!user || me.data.id !== user.id) {
        // Not the student who started this quiz: end that new session again,
        // store nothing, submit nothing.
        void api
          .post('/auth/logout', {}, { headers: { Authorization: `Bearer ${access_token}` } })
          .catch(() => {})
        setError(
          `That's a different account. Sign in as ${email} to submit this quiz. Your answers are kept.`
        )
        return
      }
      login(access_token, { id: me.data.id, email: me.data.email ?? email, role })
      onSignedIn()
    } catch (err) {
      setPassword('')
      setError(getErrorMessage(err, 'Invalid email or password.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" role="presentation">
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
      >
        <h2 id={titleId} className="modal-title">
          Sign in again to submit
        </h2>
        <p id={bodyId} className="modal-message">
          Your sign-in has expired. Your answers are still here. Sign in as {email} to submit.
        </p>
        <form className={styles.signInForm} onSubmit={handleSubmit}>
          {error && <ErrorBanner message={error} />}
          <div className="field-group">
            <label htmlFor={emailId} className="field-label">
              Email
            </label>
            <input
              id={emailId}
              type="email"
              className="field-input"
              value={email}
              readOnly
              autoComplete="username"
            />
          </div>
          <div className="field-group">
            <label htmlFor={passwordId} className="field-label">
              Password
            </label>
            <input
              ref={passwordRef}
              id={passwordId}
              type="password"
              className="field-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          <div className="modal-actions">
            <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || password === ''}>
              Sign in and submit
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
