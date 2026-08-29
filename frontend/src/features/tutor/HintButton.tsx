import { Badge } from '../../components/ui/Badge'

interface HintButtonProps {
  currentHintLevel: number
  maxHintLevel?: number
  onRequestHint: () => Promise<void>
  isLoading?: boolean
}

/**
 * Top of the ladder. Mirrors MAX_HINT_LEVEL in
 * backend/agents/tutor/prompts.py, which is the source of truth.
 *
 * FR-03.2's ladder is Guiding Question (level 0) -> Hint 1 -> Hint 2 ->
 * Hint 3 -> Full Explanation (level 4). This was 3, so the final rung was
 * unreachable from the UI and the student hit "Max hint level reached"
 * instead of ever getting the worked explanation.
 */
const MAX_HINT_LEVEL = 4

/** The terminal rung is a full worked explanation, not another hint. */
const FULL_EXPLANATION_LEVEL = MAX_HINT_LEVEL

function levelLabel(level: number): string {
  if (level >= FULL_EXPLANATION_LEVEL) return 'Full explanation'
  return String(level)
}

function buttonLabel(nextLevel: number): string {
  if (nextLevel >= FULL_EXPLANATION_LEVEL) return 'Show full explanation'
  return `Request hint level ${nextLevel}`
}

/** Button to request the next hint level. */
export function HintButton({
  currentHintLevel,
  maxHintLevel = MAX_HINT_LEVEL,
  onRequestHint,
  isLoading,
}: HintButtonProps) {
  const canRequestHint = currentHintLevel < maxHintLevel
  const nextLevel = currentHintLevel + 1

  return (
    <div className="hint-button-container">
      <div className="hint-status">
        <span className="hint-label">Hint level:</span>
        <Badge variant="info">{levelLabel(currentHintLevel)}</Badge>
      </div>
      {canRequestHint && (
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onRequestHint}
          disabled={isLoading}
        >
          {isLoading
            ? nextLevel >= FULL_EXPLANATION_LEVEL
              ? 'Working through it...'
              : 'Requesting hint...'
            : buttonLabel(nextLevel)}
        </button>
      )}
      {!canRequestHint && (
        <span className="hint-max">
          Full explanation given — ask a new question to start a fresh ladder.
        </span>
      )}
    </div>
  )
}
