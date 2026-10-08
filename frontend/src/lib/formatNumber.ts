/**
 * A score or point total for display: at most one decimal, no trailing zero.
 * 3.2142857142857144 -> "3.2", 4.5 -> "4.5", 6 -> "6", 0.04 -> "0".
 *
 * Display only. The value sent to or stored by the API is never rounded here;
 * point totals arrive as sums of floats and must not reach the screen raw.
 */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return '—'
  const rounded = Math.round((n + Math.sign(n) * Number.EPSILON) * 10) / 10
  // -0 would print as "0" anyway, but keep the sign out of it explicitly.
  return String(rounded === 0 ? 0 : rounded)
}
