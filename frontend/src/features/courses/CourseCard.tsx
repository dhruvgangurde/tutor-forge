import { Link } from 'react-router-dom'
import { Badge } from '../../components/ui/Badge'
import { statusToVariant } from '../../lib/statusVariant'
import type { CourseSummary } from '../../lib/api/types'

interface CourseCardProps {
  course: CourseSummary
}

export function CourseCard({ course }: CourseCardProps) {
  return (
    <Link to={`/courses/${course.id}`} className="card">
      <div className="card-header-row">
        <h3 className="card-title">{course.name}</h3>
        <Badge variant={statusToVariant(course.status)}>{course.status}</Badge>
      </div>
      <p className="card-meta">
        Uploaded {new Date(course.created_at).toLocaleDateString()}
      </p>
    </Link>
  )
}
