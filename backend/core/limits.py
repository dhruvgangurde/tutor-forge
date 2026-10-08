"""
core/limits.py
--------------
Maximum sizes for user-supplied text, in one place (audit 2026-10-06 #5).

Every limit is enforced by the request schema, so an oversized value is a 422
with a readable message -- never a 500 from the database or a multi-megabyte
string handed to the model. The frontend mirrors these values in
``frontend/src/lib/limits.ts`` as ``maxLength`` on the matching inputs; keep
the two files in step.

The numbers are deliberately generous: each one is well past anything a real
student or teacher types, so it only ever stops abuse.
"""

# A tutor question is a chat message, not an essay.
MAX_TUTOR_QUESTION_CHARS = 2_000

# A free-text (short-answer or numeric) answer on a submission.
MAX_ANSWER_TEXT_CHARS = 5_000

# Generation produces at most 30 questions per assessment; this leaves headroom
# without letting one request carry thousands of rows.
MAX_SUBMISSION_ANSWERS = 100

# courses.name is VARCHAR(255): longer names used to fail as a 500 on insert.
MAX_COURSE_NAME_CHARS = 255

# RFC 5321 caps a usable address at 254 characters.
MAX_EMAIL_CHARS = 254

# Unchanged from the limits the assessment schemas already enforced.
MAX_ASSESSMENT_TITLE_CHARS = 200
MAX_ASSESSMENT_TOPIC_CHARS = 300

# Output, not input: the longest course-text excerpt a tutor citation returns
# (audit 2026-10-06 #1). The model still reads whole chunks; the client only
# needs enough to recognise the passage, not the course text verbatim.
MAX_CITATION_EXCERPT_CHARS = 200
