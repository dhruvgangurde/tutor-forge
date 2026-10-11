import { useEffect, useState } from 'react'
import { ClockIcon } from '../../components/ui/icons'
import { plural } from '../../lib/plural'
import styles from './assessments.module.css'

/** Warn this long before the sign-in ends. */
const WARN_BEFORE_MS = 10 * 60_000
/** How often the remaining time is read again. */
const CHECK_EVERY_MS = 15_000

/**
 * When the stored access token expires, in ms since the epoch, or null.
 *
 * For DISPLAY ONLY: the token's payload is read, not verified. Nothing here
 * decides access; the server still checks every request.
 */
export function tokenExpiryMs(token: string | null): number | null {
  const payload = token?.split('.')[1]
  if (!payload) return null
  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')))
    return typeof claims.exp === 'number' ? claims.exp * 1000 : null
  } catch {
    return null
  }
}

/** Whole minutes left when the sign-in ends within the warning window; otherwise null. */
function minutesLeft(): number | null {
  let token: string | null = null
  try {
    token = localStorage.getItem('tf_access_token')
  } catch {
    return null
  }
  const exp = tokenExpiryMs(token)
  if (exp === null) return null
  const left = exp - Date.now()
  if (left <= 0 || left > WARN_BEFORE_MS) return null
  return Math.ceil(left / 60_000)
}

/**
 * On the quiz page, about ten minutes before the sign-in ends. Once it has
 * ended there is nothing to count down: the submit asks to sign in again
 * (SignInAgainDialog). An icon and words, never colour alone.
 */
export function SessionEndingBanner() {
  const [minutes, setMinutes] = useState(minutesLeft)

  useEffect(() => {
    const id = setInterval(() => setMinutes(minutesLeft()), CHECK_EVERY_MS)
    return () => clearInterval(id)
  }, [])

  if (minutes === null) return null
  return (
    <div className={styles.sessionBanner} role="status">
      <ClockIcon size={18} className={styles.sessionBannerIcon} />
      <p className={styles.sessionBannerText}>
        Your sign-in ends in about {plural(minutes, 'minute')}. If it ends, you can sign in again
        here and keep your answers.
      </p>
    </div>
  )
}
