"""
agents/tutor/output_guard.py
-----------------------------
Deterministic checks on a generated tutor reply, after the prompt has had its say.

Why this exists
---------------
A live replay on a small local model showed four faults that the prompts alone
did not prevent:

  1. Invented student statements: "You mentioned a formula..." on a first turn,
     when the student had said nothing of the kind.
  2. Stating the answer at the low rungs: "Potential difference is the work done
     per unit charge..." at hint level 0, where the tutor should only ask.
  3. A verdict after a non-answer: "you're on the right track" after "no".
  4. Worked-example leakage: "Example 7", "17.65 V", "figure 2.14" lifted from
     a retrieved worked example the student never saw.

Each has a prompt rule (agents/tutor/prompts.py) and a check here, on the same
principle as the context-phrasing sanitiser: an instruction the model may ignore
is not an enforcement. The checks are written as general rules over the reply,
the student's own words and the retrieved course text, not as matches for the
sentences that were seen, because a different model will phrase things
differently.

What a check does when it trips
-------------------------------
It removes the offending sentence, or only its leading clause where the rest of
the sentence is still a good question ("Not quite, but what...?"). It never
asks the model again. If nothing is left, or the low rungs or a reply are left
with no question, the reply becomes a fixed Socratic question. A reply never
comes out empty.

Last of all, a reply keeps only its first question: the model often stacked
three, which leaves the student unsure which one to answer.

No model call, no new retrieval and no change to the groundedness gate: this
only ever removes text the model produced, or replaces it with fixed wording.

Logging
-------
Only the rule name, the path and the hint level are logged. Never the reply,
the student's text or the course text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agents.tutor.prompts import (
    GUARD_FALLBACK_NON_ANSWER,
    GUARD_FALLBACK_QUESTION,
    MAX_ANSWER_GUARD_LEVEL,
)
from agents.tutor.query_resolution import topical_words

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

PATH_NEW_QUESTION = "new_question"  # a new question; the model saw only that question
PATH_HINT = "hint"                  # the hint button; the model saw only the open question
PATH_REPLY = "reply"                # a reply or follow-up; the model saw their reply too

# ── Rule names (logged) ───────────────────────────────────────────────────────

RULE_INVENTED_REFERENCE = "invented_reference"
RULE_STATES_ANSWER = "states_answer"
RULE_VERDICT_AFTER_NON_ANSWER = "verdict_after_non_answer"
RULE_EXAMPLE_REFERENCE = "example_reference"
RULE_UNUSED_NUMBER = "unused_number"
RULE_FALLBACK = "fallback"
RULE_ONE_QUESTION = "one_question"

#: Full explanation: the only rung where a worked-example qualifier ("as shown
#: in Example 7,") is cut from the sentence instead of removing the sentence.
_FULL_EXPLANATION_LEVEL = 4


@dataclass(frozen=True)
class TutorTurn:
    """What the guard needs to know about the turn the reply was written for."""

    path: str
    hint_level: int
    question: str                  # THE QUESTION: the open learning question
    course_text: str               # the retrieved passages the model was given
    student_reply: str | None = None  # THEIR REPLY, on the reply path only
    is_non_answer: bool = False    # their reply was "no", "I don't know"...


# ── Words ─────────────────────────────────────────────────────────────────────

#: Acknowledgements and verdict words that carry no content of their own. Added
#: to the retrieval filler list so "Yes, X is Y" compares on X and Y only.
_EXTRA_FILLER = frozenset({
    "yes", "right", "correct", "exactly", "good", "great", "nice", "well",
    "really", "very", "also", "which", "there", "their", "into", "only", "like",
    "let", "let's", "lets", "other", "each", "such", "own", "words",
})


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _content_words(text: str) -> set[str]:
    return {_stem(w) for w in topical_words(text) if w not in _EXTRA_FILLER and not w.isdigit()}


_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text or "")}


def _overlap(words: set[str], other: set[str]) -> float:
    return len(words & other) / len(words) if words else 0.0


# ── Sentences ─────────────────────────────────────────────────────────────────
#
# The reply is markdown: paragraphs, sometimes a list. It is split into lines,
# each line into an optional list marker and its sentences, so a sentence can be
# removed without disturbing the rest of the layout.

_ABBREVIATIONS = frozenset({
    "fig", "figs", "eq", "eqs", "p", "pp", "e.g", "i.e", "eg", "ie", "vs", "etc",
    "no", "approx", "ex", "cf", "dr", "mr", "mrs", "st",
})
_BOUNDARY_RE = re.compile(r"[.!?]+[\"'”’)\]*_]*\s+")
_MARKER_RE = re.compile(r"^(\s*(?:[-*+>]|\d+[.)]|#{1,6})\s+)")
_STYLE_RE = re.compile(r"[*_`]")


def _split_sentences(body: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for m in _BOUNDARY_RE.finditer(body):
        nxt = body[m.end():m.end() + 1]
        if not nxt or nxt.islower() or nxt.isdigit():
            continue
        before = body[start:m.start()].split()
        if body[m.start()] == "." and before and before[-1].lower().rstrip(".") in _ABBREVIATIONS:
            continue
        sentences.append(body[start:m.end()].strip())
        start = m.end()
    tail = body[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


@dataclass
class _Line:
    marker: str
    sentences: list[str] = field(default_factory=list)


def _parse(text: str) -> list[_Line | None]:
    """Lines of the reply; None stands for a blank line (a paragraph break)."""
    lines: list[_Line | None] = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append(None)
            continue
        m = _MARKER_RE.match(raw)
        marker = m.group(1) if m else ""
        lines.append(_Line(marker=marker, sentences=_split_sentences(raw[len(marker):])))
    return lines


def _render(lines: list[_Line | None]) -> str:
    out: list[str] = []
    for line in lines:
        if line is None:
            out.append("")
        elif line.sentences:
            out.append(line.marker + " ".join(line.sentences))
        # A line whose sentences were all removed disappears, marker and all.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _plain(sentence: str) -> str:
    """The sentence without markdown emphasis, for matching only."""
    return _STYLE_RE.sub("", sentence).strip()


def _is_question(sentence: str) -> bool:
    return _plain(sentence).rstrip("\"'”’) ").endswith("?")


def _capitalise(text: str) -> str:
    text = text.lstrip(" ,;:-—")
    return text[:1].upper() + text[1:] if text else text


_LEAD_CONNECTOR_RE = re.compile(r"^(?:but|and|so|however|though|still|yet)\b[,\s]*", re.IGNORECASE)


def _cut_leading_clause(sentence: str, offends) -> str | None:
    """
    Drop leading clauses (up to a comma, dash or semicolon) that ``offends``,
    keeping the rest when the rest is still a question. None when nothing is
    left worth keeping.
    """
    rest = sentence
    cut = False
    while True:
        parts = re.split(r"\s*(?:,|;|\s[-—]\s)\s*", rest, maxsplit=1)
        if len(parts) < 2 or not offends(parts[0]):
            break
        rest = _LEAD_CONNECTOR_RE.sub("", parts[1])
        cut = True
    if not cut:
        return sentence
    if not offends(rest) and _is_question(rest):
        return _capitalise(rest)
    return None


# ── 1. Invented student statements ────────────────────────────────────────────

#: Phrases that attribute something to the student. "You asked..." is not here:
#: on every path the student did ask the question.
_SAID_VERBS = (
    r"mentioned|said|noted|stated|suggested|pointed\s+out|identified|described|"
    r"explained|proposed|wrote|brought\s+up|indicated|guessed|figured\s+out|realised|realized"
)
_BACKREF_RE = re.compile(
    r"\b(?:you(?:'ve|\s+have|\s+had|\s+already|\s+just|\s+correctly|\s+rightly)*\s+(?:" + _SAID_VERBS + r")"
    r"|as\s+you(?:'ve|\s+have)?\s+(?:" + _SAID_VERBS + r"|put\s+it)"
    r"|(?:the|that|this|your)\s+(?:example|formula|equation|definition|idea|point)\s+you\s+"
    r"(?:provided|gave|mentioned|used|wrote|shared|described|suggested|came\s+up\s+with)"
    r")\b",
    re.IGNORECASE,
)
#: References to an earlier turn. The model only ever sees the latest reply, so
#: it cannot know what was said before it.
_EARLIER_RE = re.compile(
    r"\b(?:your|you)\s+(?:earlier|previous|last|first|initial|original|prior)\s+"
    r"(?:answer|reply|response|point|idea|suggestion|guess|thought|attempt|statement|example|comment)"
    r"|\b(?:you\s+(?:said|mentioned|suggested)\s+(?:earlier|before|previously))",
    re.IGNORECASE,
)
#: "Your answer is partly right": fine when there is a reply, invented when not.
_YOUR_REPLY_RE = re.compile(
    r"\byour\s+(?:answer|reply|response|guess|attempt|idea|suggestion|example|thinking)\b",
    re.IGNORECASE,
)


def _attributed_words(sentence: str, match: re.Match) -> set[str]:
    """The content words of the clause the back-reference attributes to them."""
    rest = sentence[match.end():].lstrip().removeprefix(",")
    rest = re.split(r"[,;]|\s[-—]\s", rest, maxsplit=1)[0]
    return _content_words(rest)


def _invented_reference(sentence: str, turn: TutorTurn, reply_words: set[str]) -> bool:
    plain = _plain(sentence)
    if _EARLIER_RE.search(plain):
        return True
    has_reply = turn.path == PATH_REPLY and not turn.is_non_answer and bool(turn.student_reply)
    if _YOUR_REPLY_RE.search(plain) and not has_reply:
        return True
    m = _BACKREF_RE.search(plain)
    if not m:
        return False
    if not has_reply:
        return True
    attributed = _attributed_words(plain, m)
    # Kept only when what is attributed is what they actually wrote.
    return not (attributed and _overlap(attributed, reply_words) >= 0.5)


# ── 2. Stating the answer (hint levels 0-2 only) ──────────────────────────────

_DEFINING_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:simply\s+|basically\s+|actually\s+|just\s+)?"
    r"(?:defined\s+as|the|a|an|equal\s+to|measured\s+in|given\s+by|calculated\s+(?:as|by|from)|"
    r"called|known\s+as|how|what|when|where|why)\b"
    r"|\b(?:means|refers\s+to|stands\s+for|represents|describes\s+how)\b"
    r"|\s=\s",
    re.IGNORECASE,
)
_SUBORDINATE_RE = re.compile(
    r"^(?:since|given\s+that|because|as|knowing\s+that|remember\s+that|recall\s+that|"
    r"if\s+we\s+know\s+that|now\s+that)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _AnswerSignals:
    topic: set[str]
    needed: int
    course_sentences: list[set[str]]   # non-topic words of course sentences on the topic
    reply_words: set[str]


def _answer_signals(turn: TutorTurn, reply_words: set[str]) -> _AnswerSignals:
    topic = _content_words(turn.question)
    needed = min(2, len(topic))
    course_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", turn.course_text or ""):
        words = _content_words(sentence)
        if needed and len(words & topic) >= needed:
            course_sentences.append(words - topic)
    return _AnswerSignals(topic, needed, course_sentences, reply_words)


def _states_answer(statement: str, signals: _AnswerSignals) -> bool:
    """A statement that tells them what the question asks, in definition form or in the course's own words."""
    plain = _SUBORDINATE_RE.sub("", _plain(statement))
    words = _content_words(plain)
    if not signals.needed or len(words & signals.topic) < signals.needed:
        return False
    rest = words - signals.topic
    # Confirming a correct attempt in their own words is not giving it away.
    if rest and _overlap(rest, signals.reply_words) >= 0.6:
        return False
    if _DEFINING_RE.search(plain):
        return True
    return len(rest) >= 3 and any(_overlap(rest, course) >= 0.6 for course in signals.course_sentences)


# ── 3. A verdict after a non-answer ───────────────────────────────────────────

_VERDICT_RE = re.compile(
    r"^(?:great|good|nice|excellent|perfect|exactly|correct|right|precisely|absolutely|"
    r"wonderful|fantastic|awesome|brilliant|well\s+done|bravo|interesting|close|almost|"
    r"not\s+quite|not\s+exactly|nearly)\b"
    r"|\b(?:great|good|nice|excellent|perfect|interesting)\s+(?:job|work|thinking|try|effort|"
    r"start|question|point|answer|guess|attempt|thought|observation|instinct)\b"
    r"|\b(?:on\s+the\s+right\s+track|getting\s+(?:there|closer|warmer)|almost\s+there|nearly\s+there)\b"
    r"|\byou(?:'re|\s+are|\s+were)\s+(?:right|correct|close|wrong|incorrect|mistaken)\b"
    r"|\b(?:that'?s|that\s+is|it'?s|it\s+is|this\s+is)\s+(?:not\s+)?(?:quite\s+)?"
    r"(?:right|correct|incorrect|wrong|close|true|a\s+good|an\s+interesting|a\s+great)\b"
    r"|\bnot\s+quite\b"
    r"|\byou\s+(?:didn'?t|did\s+not|haven'?t|have\s+not)\s+(?:quite\s+)?(?:answer|get|got)\b",
    re.IGNORECASE,
)


def _is_verdict(text: str) -> bool:
    return bool(_VERDICT_RE.search(_plain(text).lstrip(" \"'")))


# ── 4. Worked-example leakage ─────────────────────────────────────────────────

_REF = (
    r"(?:example|ex\.|figure|fig\.?|table|exercise|problem|equation|eq\.?)\s*\(?\d+(?:[.\-]\d+)*\)?"
    r"|(?:page|pages|p\.|pp\.)\s*\d+"
)
_EXAMPLE_REF_RE = re.compile(r"\b(?:" + _REF + r")", re.IGNORECASE)
#: A reference used only as a qualifier, which the full explanation may lose
#: without losing its sentence.
_REF_QUALIFIER_RE = re.compile(
    r"\s*\((?:see\s+|as\s+in\s+|from\s+)?(?:the\s+)?(?:" + _REF + r")\)"
    r"|,?\s*\b(?:as\s+(?:shown|seen|illustrated|described|worked\s+out|given|calculated)\s+in|"
    r"see|in|from|like\s+in|using)\s+(?:the\s+)?(?:" + _REF + r")\s*,?",
    re.IGNORECASE,
)


def _cut_reference_qualifier(sentence: str) -> str | None:
    out = _REF_QUALIFIER_RE.sub("", sentence)
    out = re.sub(r"\s*,\s*([.!?])", r"\1", out).strip()
    if not out or _EXAMPLE_REF_RE.search(out) or not _content_words(out):
        return None
    return _capitalise(out)


# ── The guard ─────────────────────────────────────────────────────────────────


def guard_tutor_reply(text: str, turn: TutorTurn) -> str:
    """Apply the checks and the one-question rule to a sanitised reply; log each rule that tripped."""
    out, tripped = apply_guards(text, turn)
    for rule in tripped:
        logger.info("[TUTOR-GUARD] rule=%s path=%s hint_level=%s", rule, turn.path, turn.hint_level)
    return out


def apply_guards(text: str, turn: TutorTurn) -> tuple[str, list[str]]:
    """Return the guarded reply and the names of the rules that changed it."""
    lines = _parse(text or "")
    tripped: list[str] = []
    reply_words = _content_words(turn.student_reply or "") if turn.path == PATH_REPLY else set()
    student_numbers = _numbers(turn.question) | _numbers(turn.student_reply or "")
    course_numbers = _numbers(turn.course_text) - student_numbers
    low_rung = turn.hint_level <= MAX_ANSWER_GUARD_LEVEL
    signals = _answer_signals(turn, reply_words) if low_rung else None

    def run(rule: str, fix) -> None:
        changed = False
        for line in lines:
            if line is None:
                continue
            kept = []
            for sentence in line.sentences:
                new = fix(sentence)
                if new != sentence:
                    changed = True
                if new:
                    kept.append(new)
            line.sentences = kept
        if changed:
            tripped.append(rule)

    # 1. Only what they actually said.
    run(RULE_INVENTED_REFERENCE, lambda s: None if _invented_reference(s, turn, reply_words) else s)

    # 2. Ask, do not tell -- below Hint 3 only.
    if signals is not None:
        def no_answer(s: str) -> str | None:
            if not _is_question(s):
                return None if _states_answer(s, signals) else s
            return _cut_leading_clause(s, lambda clause: _states_answer(clause, signals))

        run(RULE_STATES_ANSWER, no_answer)

    # 3. No verdict on a non-answer.
    if turn.is_non_answer:
        def no_verdict(s: str) -> str | None:
            if not _is_verdict(s):
                return s
            if not _is_question(s):
                return None
            cut = _cut_leading_clause(s, _is_verdict)
            # Unchanged means the verdict is inside the question itself.
            return None if cut == s else cut

        run(RULE_VERDICT_AFTER_NON_ANSWER, no_verdict)

    # 4a. No example, figure, table or page numbers.
    def no_reference(s: str) -> str | None:
        if not _EXAMPLE_REF_RE.search(_plain(s)):
            return s
        if turn.hint_level >= _FULL_EXPLANATION_LEVEL:
            return _cut_reference_qualifier(s)
        return None

    run(RULE_EXAMPLE_REFERENCE, no_reference)

    # 4b. No numbers lifted from the course that they did not use -- below
    # Hint 3 only; the higher rungs may work through the numbers.
    if low_rung and course_numbers:
        run(RULE_UNUSED_NUMBER, lambda s: None if _numbers(_plain(s)) & course_numbers else s)

    if not _render(lines) or (
        tripped
        and (low_rung or turn.path == PATH_REPLY)
        and not any(_is_question(s) for line in lines if line for s in line.sentences)
    ):
        tripped.append(RULE_FALLBACK)
        return (GUARD_FALLBACK_NON_ANSWER if turn.is_non_answer else GUARD_FALLBACK_QUESTION), tripped

    # 6. One question only, checked last so a question an earlier rule removed
    # does not count. The first is kept, so a reply that had a question still
    # has one, and statements are never touched.
    seen_question = False

    def first_question_only(s: str) -> str | None:
        nonlocal seen_question
        if not _is_question(s):
            return s
        if seen_question:
            return None
        seen_question = True
        return s

    run(RULE_ONE_QUESTION, first_question_only)
    return _render(lines), tripped
