"""
core/context_phrasing.py
-------------------------
Strip references to "the context" from user-facing agent output.

The problem
-----------
Both the assessment generator and the tutor are handed retrieved course
material in a block labelled COURSE CONTEXT, and both leak that framing into
text a student reads:

    "When did humans emerge in Africa according to the context?"
    "the course context provided does not directly mention..."

A student never sees "the context" and has no idea what it refers to. It is an
implementation detail of the prompt showing through.

Measured on live data before this existed: 9 of 25 stored question stems and
3 of 26 tutor messages contained it.

Why a sanitiser and not just a prompt rule
------------------------------------------
Both prompts now carry an explicit negative instruction, but prompt-following
is exactly what already failed here — the assessment prompt's existing rules did
not stop it. So the instruction is the first line and this is the second, on the
same principle as the grading groundedness gate: an instruction the model may
ignore is not an enforcement.

The idiom trap
--------------
"In the context of dynamic programming, what does DP stand for?" is ordinary
English meaning "within the domain of", and a naive strip mangles it into "Of
dynamic programming, what does DP stand for?". That phrasing is left alone: only
references to *the supplied material as a document* are removed.
"""

from __future__ import annotations

import re

# Qualifier phrases that point at the prompt's context block. Each is written to
# swallow a leading comma/space so removing it does not leave " ,".
#
# Ordered longest-first: "based on the information provided in the course
# context" must match before the shorter "in the context" fragment inside it.
_QUALIFIERS = [
    r"(?:as\s+)?(?:described|stated|mentioned|shown|given|outlined|noted)\s+in\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?",
    r"based\s+on\s+the\s+(?:information\s+)?(?:provided\s+)?in\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?",
    r"based\s+on\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?",
    r"based\s+on\s+the\s+information\s+provided",
    r"according\s+to\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?",
    r"accordingly\s+to\s+the\s+context",
    r"from\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?",
    r"per\s+the\s+(?:provided\s+|course\s+|given\s+)?context",
    # "in the context" only when NOT the idiom "in the context of <topic>".
    r"in\s+the\s+(?:provided\s+|course\s+|given\s+)?context(?!\s+of\b)(?:\s+provided)?",
]

#: A qualifier, optionally preceded by a comma, anywhere in the sentence.
_QUALIFIER_RE = re.compile(
    r"\s*,?\s*(?:" + "|".join(_QUALIFIERS) + r")",
    re.IGNORECASE,
)

# Subject phrases: the context itself as the actor ("the context states that").
# Removing the whole clause would delete content, so only the subject is
# rewritten, leaving the claim intact.
_SUBJECT_RE = re.compile(
    r"\bthe\s+(?:provided\s+|course\s+|given\s+)?context\s+"
    r"(?:provided\s+)?"
    r"(?:states|says|provides|mentions|indicates|shows|describes|explains|notes|lists)\s+"
    r"(?:that\s+)?",
    re.IGNORECASE,
)

#: "the course context provided does not mention" -> "the course material does
#: not cover". Negations are kept as sentences because deleting them would
#: change the meaning of a refusal; only the wording is made student-facing.
_NEGATION_RE = re.compile(
    r"\bthe\s+(?:provided\s+|course\s+|given\s+)?context(?:\s+provided)?\s+"
    r"(?:does\s+not|doesn't|did\s+not|didn't)\s+"
    r"(?:directly\s+)?(?:mention|cover|state|include|provide|discuss|say)",
    re.IGNORECASE,
)

#: Bare noun phrase, for the detector only.
_MENTION_RE = re.compile(
    r"\b(?:the\s+)?(?:provided\s+|course\s+|given\s+)?context\b(?!\s+of\b)",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")


def mentions_context(text: str) -> bool:
    """
    True when ``text`` refers to the prompt's context block.

    Used to flag output for logging even when the sanitiser leaves it alone, so
    a phrasing this module does not yet handle is still visible rather than
    silently shipped. The "in the context of <topic>" idiom does not count.
    """
    if not text:
        return False
    return bool(_MENTION_RE.search(text))


def strip_context_references(text: str) -> str:
    """
    Remove references to the prompt's context block, preserving meaning.

    Order matters: negations are rewritten first (they are whole claims), then
    subject phrases, then trailing qualifiers — otherwise the short qualifier
    pattern would eat a fragment of a longer phrase and leave debris.
    """
    if not text:
        return text

    out = _NEGATION_RE.sub("the course material does not cover", text)
    out = _SUBJECT_RE.sub("", out)
    out = _QUALIFIER_RE.sub("", out)

    # Tidy the seams the removals leave behind.
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _WS_RE.sub(" ", out).strip()
    # A qualifier that opened the sentence leaves its trailing comma stranded
    # ("as described in the context, the crust is thin" -> ", the crust ...").
    out = out.lstrip(",;: ").strip()

    # A removal at the start can leave a lowercase opener ("the context states
    # that humans emerged" -> "humans emerged"); restore sentence case.
    if out and out[0].islower() and text[:1].isupper():
        out = out[0].upper() + out[1:]
    return out
