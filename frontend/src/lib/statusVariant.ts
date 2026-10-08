import type { BadgeVariant } from '../components/ui/Badge'

/**
 * Maps backend status strings (course ingestion, assessment lifecycle, grading)
 * to a Badge variant. Covers the union of known statuses across features;
 * unrecognized values fall back to 'muted' rather than throwing.
 */
const STATUS_LABELS: Record<string, string> = {
  pending_review: 'Pending review',
  pending_grading: 'Awaiting grading',
  approved: 'Approved',
  overridden: 'Overridden',
  // Course ingestion
  pending: 'Pending',
  ingesting: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
  // Assessment lifecycle
  generating: 'Generating',
  draft: 'Draft',
  published: 'Published',
  graded: 'Graded',
  // Question types
  mcq: 'Multiple choice',
  short_answer: 'Short answer',
  numeric: 'Numeric',
}

/**
 * Human-readable text for a backend status value. Badges used to print the raw
 * value, so a grading item showed as "PENDING_REVIEW". Unknown values keep
 * their wording with underscores turned into spaces.
 */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replace(/_/g, ' ')
}

/**
 * Chip colour by meaning (design brief, semantic colour rules): accent for
 * ready/published/released, amber for anything awaiting review, red only for
 * errors, warm grey for neutral states. An override is a teacher's decision,
 * not an error, so it is neutral rather than red.
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
    case 'pending_review':
    case 'pending_grading':
      return 'warning'
    case 'failed':
      return 'danger'
    case 'pending':
    case 'draft':
    case 'overridden':
    default:
      return 'muted'
  }
}
