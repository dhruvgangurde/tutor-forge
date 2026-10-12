/**
 * Maximum input sizes, mirroring backend/core/limits.py (keep the two in step).
 *
 * The server enforces these with a 422; setting them as `maxLength` on the
 * matching inputs means a user simply cannot type past them, and
 * getErrorMessage turns the 422 into a readable sentence if one gets through.
 */
export const MAX_TUTOR_QUESTION_CHARS = 2_000
export const MAX_ANSWER_TEXT_CHARS = 5_000
export const MAX_COURSE_NAME_CHARS = 255
export const MAX_EMAIL_CHARS = 254
export const MAX_ASSESSMENT_TITLE_CHARS = 200
export const MAX_ASSESSMENT_TOPIC_CHARS = 300

/**
 * All files in one course upload together, in bytes (backend MAX_UPLOAD_TOTAL_BYTES:
 * the server answers 413 past it). Checked before sending so a teacher is told at once.
 */
export const MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
