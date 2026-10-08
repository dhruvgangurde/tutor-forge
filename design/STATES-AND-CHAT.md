# Tutor chat and edge states (2026-10-08)

Extends DESIGN-BRIEF.md. Styling and copy only; nothing here changes behaviour. Use these where the corresponding screen already exists.

## 1. Tutor chat
- **Empty chat:** a serif prompt line ("What would you like to work through today?") plus the one-line note that the tutor asks questions rather than giving answers and only uses the course material. NO suggestion chips (the product has none).
- **Tutor message:** plain text, small uppercase "TUTOR" label with the book mark. Student message in a `--surface-2` bubble.
- **Generating state:** a quiet "Thinking" line with three small dots in the tutor slot. Under `prefers-reduced-motion`, static text only. No full-page spinner.
- **Chat error:** a contained card under the failed message, plain wording through `getErrorMessage`, and a "Try again" button ONLY where resending is already supported; otherwise just the message and keep the input filled.
- **Composer:** docked at the bottom, Hint and Send on the same row. Hint stays disabled until the first question, with the existing reason text ("Available after your first question" in the mockup).
- **Citation chips:** restyle only. No popover in the restyle.

## 2. Chat accessibility (polish phase, with tests)
- Message history: `role="log"` with a label and `tabindex="0"` so keyboard users can scroll it.
- Do not read streaming text aloud chunk by chunk. One separate, always-present `role="status"` element announces lifecycle events only: "Tutor is replying", "Reply ready", "Something went wrong". Polite, not assertive.
- Keep focus in the input after sending; never move it to the reply.
- Give each message a visually hidden sender label ("You", "Tutor").
- Any Retry control is a normal keyboard-reachable button.
- Check what the page already does before changing it.

## 3. Loading, empty and error states
**404 / not found:** keep the app navigation visible; serif heading, one short explanation, one clear primary action ("Back to your courses"), optional secondary link. Never show raw URL or id text.

**Empty lists:** say what is empty, why, and what to do. Quiet, in the normal page layout, no big illustration. Draft copy (use only where the screen exists):
- Student, no courses: "No courses yet. Your teacher will add you to a course, and it will appear here."
- Student, no quizzes in a course: "No quizzes yet. Your teacher hasn't published one for this course." (quiet, informational, not a warning)
- Teacher, no assessments: "No assessments yet. Generate one from this course's material." with the Generate button.
- Teacher, no students: "No students enrolled. Add a student by email to give them access."
- Grading, nothing pending: "Nothing waiting for your review."

**Loading:** pale skeleton placeholders shaped like the final content (cards for course lists, rows for tables), not a full-page spinner. Never leave a spinner or skeleton on screen once content is known to be empty. Very long jobs (assessment generation, course ingestion) keep their existing progress or polling state, in plain words. Skeleton shimmer is static under `prefers-reduced-motion`.

**Inline errors:** a contained card or banner next to the thing that failed, plain message, one recovery action ("Try again") only where retrying is possible. Soft red background, red only on the icon and heading, icon plus words (never colour alone). Never show raw backend text; always render the output of `getErrorMessage`.

## 4. Where this plugs in
- Phase 2 (shared components): skeleton, empty state, inline error card, 404 layout.
- Phase 3 (tutor page): empty chat, thinking state, error card.
- Phase 6 (polish): the chat accessibility list.
