import type { BadgeVariant } from '../components/ui/Badge'

/**
 * Maps backend status strings (course ingestion, assessment lifecycle, grading)
 * to a Badge variant. Covers the union of known statuses across features;
 * unrecognized values fall back to 'muted' rather than throwing.
 */
export function statusToVariant(status: string): BadgeVariant {
  switch (status) {
    case 'ready':
    case 'published':
    case 'approved':
      return 'success'
    case 'ingesting':
    case 'generating':
      return 'info'
    case 'pending':
    case 'draft':
    case 'pending_review':
    case 'pending_grading':
      return 'muted'
    case 'failed':
    case 'overridden':
      return 'danger'
    default:
      return 'muted'
  }
}
