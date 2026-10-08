import { formatNumber } from './formatNumber'

/**
 * A count with its noun: "1 assessment", "0 assessments", "2 assessments".
 * Replaces "assessment(s)" and the hand-rolled `!== 1 ? 's' : ''` checks.
 */
export function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  // Points can be fractional sums; the number goes through the display rounding.
  return `${formatNumber(n)} ${n === 1 ? singular : pluralForm}`
}
