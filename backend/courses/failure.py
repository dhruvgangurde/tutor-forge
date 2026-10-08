"""
courses/failure.py
------------------
A short, plain-language reason for a failed course (frontend audit #11).

Ingestion already records why it failed in ``ingestion_jobs.error_message``,
but that text is written for logs ("Hierarchy JSON parse error: Expecting ','
delimiter: line 1 column 312") and was never returned to the teacher, whose
course card just said "failed". This turns it into one sentence the teacher
can act on. The raw message stays in the database for debugging.
"""

from __future__ import annotations

_DEFAULT = "Processing the course materials failed. Try uploading the course again."

# (prefix of the stored message, what the teacher is told)
_REASONS: tuple[tuple[str, str], ...] = (
    (
        "Unsupported file type",
        "One of the files is not a supported type. Upload PDF, PowerPoint or text files.",
    ),
    ("File too large", "One of the files is larger than the 50 MB limit."),
    (
        "Hierarchy JSON parse error",
        "The course outline could not be worked out from these materials. "
        "Try uploading the course again.",
    ),
    (
        "Ingestion did not complete",
        "Processing was interrupted before it finished. Upload the course again.",
    ),
)


def failure_reason(error_message: str | None) -> str:
    """The teacher-facing reason for a failed ingestion."""
    message = (error_message or "").strip()
    for prefix, reason in _REASONS:
        if message.startswith(prefix):
            return reason
    return _DEFAULT
