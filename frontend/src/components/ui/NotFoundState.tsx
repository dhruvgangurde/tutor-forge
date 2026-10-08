import { Link } from 'react-router-dom'
import { ArrowLeftIcon } from './icons'

interface NotFoundStateProps {
  title: string
  message: string
  linkTo: string
  linkLabel: string
}

/**
 * 404 layout (design/STATES-AND-CHAT.md): rendered inside the app shell so the
 * navigation stays visible; a serif heading, one plain explanation and one
 * primary "back" action. Never shows the raw URL or id.
 */
export function NotFoundState({ title, message, linkTo, linkLabel }: NotFoundStateProps) {
  return (
    <section className="not-found">
      <h1 className="not-found-title">{title}</h1>
      <p className="not-found-message">{message}</p>
      <Link to={linkTo} className="btn btn-primary">
        <ArrowLeftIcon size={16} />
        {linkLabel}
      </Link>
    </section>
  )
}
