/**
 * A count with its noun: "1 assessment", "0 assessments", "2 assessments".
 * Replaces "assessment(s)" and the hand-rolled `!== 1 ? 's' : ''` checks.
 */
export function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${n} ${n === 1 ? singular : pluralForm}`
}
