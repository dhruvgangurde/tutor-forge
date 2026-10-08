import { Badge } from '../../components/ui/Badge'
import { statusLabel } from '../../lib/statusVariant'
import type { CourseStructure } from '../../lib/api/types'

interface ConceptTreeProps {
  structure: CourseStructure
}

/** Renders the chapter → concept hierarchy for a ready course. */
export function ConceptTree({ structure }: ConceptTreeProps) {
  if (structure.chapters.length === 0) {
    return <p className="page-subtitle">No chapters were extracted from this course yet.</p>
  }

  return (
    <div className="concept-tree">
      {structure.chapters
        .slice()
        .sort((a, b) => a.order_index - b.order_index)
        .map((chapter) => (
          <div key={chapter.id} className="concept-chapter">
            <h3 className="concept-chapter-title">{chapter.title}</h3>
            <ul className="concept-list">
              {chapter.concepts
                .slice()
                .sort((a, b) => a.order_index - b.order_index)
                .map((concept) => (
                  <li key={concept.id} className="concept-item">
                    <span className="concept-name">{concept.name}</span>
                    {concept.difficulty && (
                      <Badge variant="info">{statusLabel(concept.difficulty)}</Badge>
                    )}
                    {concept.description && (
                      <p className="concept-description">{concept.description}</p>
                    )}
                  </li>
                ))}
            </ul>
          </div>
        ))}
    </div>
  )
}
