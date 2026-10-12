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
    # Sentence-aware: a phrase that opened a later sentence used to leave
    # "difference., potential difference is..." behind.
    starts_upper = text[:1].isupper()
    out = _remove_keeping_sentence_case(_SUBJECT_RE, out, capitalise_start=starts_upper)
    out = _remove_keeping_sentence_case(_QUALIFIER_RE, out, capitalise_start=starts_upper)

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


# ── Tutor only: narrating the source ──────────────────────────────────────────
#
# The tutor opened nearly every turn with "the course material states / mentions
# / says". The student already sees the cited sources under the reply, so the
# narration is noise. Kept out of strip_context_references on purpose: that one
# also cleans assessment stems, which this change is not about.

#: The course, its material and its lectures, by name: "the course", "our
#: course material", "the course notes", "the lecture slides", "your lecture
#: notes".
_COURSE_SUFFIX = r"(?:\s+(?:materials?|notes|content|slides|readings?))"
_LECTURE = r"(?:the|your|our|this)\s+lectures?(?:\s+(?:notes|slides))?"
#: The article is optional here, as before ("based on course notes").
_COURSE = (
    r"(?:(?:(?:the|your|our|this)\s+)?course" + _COURSE_SUFFIX + r"?|" + _LECTURE + r")\b"
)
#: With an article, for "from ...": "from course to course" is left alone.
_COURSE_NAMED = (
    r"(?:(?:the|your|our|this)\s+course" + _COURSE_SUFFIX + r"?|" + _LECTURE + r")\b"
)
#: Material only, for a closing "in ...": "covered in the course." is ordinary
#: English, "as you saw in the course notes." is narration.
_COURSE_MATERIAL = (
    r"(?:(?:the|your|our|this)\s+course" + _COURSE_SUFFIX + r"|" + _LECTURE + r")\b"
)
#: Singular and plural: "the lecture slides say", "the passages show".
_NARRATING_VERB = (
    r"(?:states?|says|say|mentions?|notes?|explains?|describes?|indicates?|shows?|"
    r"tells?\s+us|points?\s+out)"
)

#: "the course material states that X" -> "X". The claim is kept. Not before
#: how / why / what...: "the lecture explains how charges move" keeps no claim.
_SOURCE_SUBJECT_RE = re.compile(
    r"\b" + _COURSE + r"\s+" + _NARRATING_VERB + r"\s+(?:that\s+)?"
    r"(?!(?:how|why|what|when|where|which|whether|who)\b)",
    re.IGNORECASE,
)

#: "the course material provided", "the passages given": the trailing word went
#: unmatched and was left capitalised ("Provided, there is no mention of...").
#: "given that" is a conjunction and stays.
_PROVIDED = (
    r"(?:\s+(?:provided|supplied|given(?!\s+that\b))"
    r"(?:\s+by\s+(?:your|the|our)\s+teacher)?)?"
)

#: "according to the course material, X" / "based on the course material, X" /
#: "as the course mentions, X" / "as mentioned in the course material" /
#: "the key idea from the course material about..." / "X, as you saw in the
#: lecture notes." -> removed.
_SOURCE_QUALIFIER_RE = re.compile(
    r"\s*,?\s*(?:"
    r"(?:according\s+to|based\s+on|as\s+per)\s+" + _COURSE + _PROVIDED
    + r"|from\s+" + _COURSE_NAMED + _PROVIDED
    + r"|as\s+" + _COURSE + r"\s+" + _NARRATING_VERB
    + r"|as\s+(?:stated|mentioned|described|explained|noted|shown|given|outlined)\s+in\s+"
    + _COURSE
    + r"|in\s+" + _COURSE_MATERIAL + r"(?=\s*[,.?!])"
    + r")",
    re.IGNORECASE,
)

#: The material by other names. "the text", "the reading", "the section" and
#: "the material" are only removed in the narrating forms below, never on
#: sight: in a literature course "the text" can be the novel itself.
_SOURCE = (
    r"(?:the|this)\s+(?:passages?|text|reading|section|excerpt|extract|materials?|notes|"
    r"document|sources?)\b"
)

#: "according to the passage, X" / "X, as described in the text" / "X in the
#: reading." / "based on the description of <...>" (to the end of its clause) /
#: "based on the description?" /
#: a sentence-final "in this context" -> removed.
_SOURCE_NOUN_QUALIFIER_RE = re.compile(
    r"\s*,?\s*(?:"
    r"(?:according\s+to|based\s+on|as\s+per|per|from)\s+" + _SOURCE + _PROVIDED
    + r"|as\s+(?:stated|described|mentioned|explained|noted|shown|given|outlined)\s+in\s+" + _SOURCE
    + r"|in\s+" + _SOURCE + r"(?=\s*[,.?!])"
    + r"|(?:according\s+to|based\s+on)\s+the\s+description\s+of\b[^,.?!\n]*"
    # "...means based on the description?": the bare form, only where it opens
    # or closes a clause, so "write a description of..." is never touched.
    + r"|(?:according\s+to|based\s+on|from|in|using|given)\s+the\s+description"
    + r"(?:\s+(?:provided|given|above))?(?!\s+of\b)(?=\s*[,.?!])"
    + r"|in\s+this\s+context(?=\s*[?.])"
    + r")",
    re.IGNORECASE,
)

#: "the passage says that X" -> "X". The claim is kept. Not before how / why /
#: what...: "the section explains how charges move" has no claim left to keep.
_SOURCE_NOUN_SUBJECT_RE = re.compile(
    r"\b" + _SOURCE + r"\s+(?:" + _NARRATING_VERB + r"|suggests|defines)\s+(?:that\s+)?"
    r"(?!(?:how|why|what|when|where|which|whether|who)\b)",
    re.IGNORECASE,
)

#: For the detector only: the tutor talking about the person it is talking to.
_THIRD_PERSON_RE = re.compile(r"\bthe\s+(?:student|learner)(?:'s)?\b", re.IGNORECASE)


def strip_source_narration(text: str) -> str:
    """Remove "the course material states that…" style narration, keeping the claim."""
    if not text:
        return text
    out = text
    for pattern in (
        _SOURCE_QUALIFIER_RE,
        _SOURCE_NOUN_QUALIFIER_RE,
        _SOURCE_SUBJECT_RE,
        _SOURCE_NOUN_SUBJECT_RE,
    ):
        out = _remove_keeping_sentence_case(pattern, out)
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _WS_RE.sub(" ", out).strip()
    return out


def _remove_keeping_sentence_case(
    pattern: re.Pattern, text: str, capitalise_start: bool = True
) -> str:
    """
    Delete each match. When a match opened a sentence, capitalise what now
    opens it and drop the comma a leading qualifier leaves behind
    ("According to the course, like charges repel" -> "Like charges repel").

    ``capitalise_start=False`` leaves the very start of the text as it was
    written, for callers that also clean lowercase fragments.
    """
    out = ""
    pos = 0
    capitalise = False

    def take(segment: str) -> str:
        nonlocal capitalise
        if capitalise and segment:
            segment = segment[0].upper() + segment[1:]
            capitalise = False
        return segment

    for m in pattern.finditer(text):
        out += take(text[pos:m.start()])
        pos = m.end()
        kept = out.rstrip()
        if not kept or kept[-1] in ".!?\n":
            while pos < len(text) and text[pos] in ",;: ":
                pos += 1
            capitalise = bool(kept) or capitalise_start
            if kept and out == kept:
                out += " "  # the pattern ate the space after the previous sentence
    return out + take(text[pos:])


def refers_to_learner_in_third_person(text: str) -> bool:
    """True when the reply calls the person it is talking to "the student"."""
    return bool(text) and bool(_THIRD_PERSON_RE.search(text))
