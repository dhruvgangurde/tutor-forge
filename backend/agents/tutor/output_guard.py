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
with no question, the reply becomes a fixed Socratic question. A reply to a
real attempt below Hint 3 keeps its untouched opening (at most two sentences,
such as "That's not quite right.") ahead of that question. A reply never comes
out empty.

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
from collections import Counter
from dataclasses import dataclass, field

from agents.tutor.prompts import (
    GUARD_FALLBACK_NON_ANSWER,
    GUARD_FALLBACK_QUESTION,
    MAX_ANSWER_GUARD_LEVEL,
    OUT_OF_CORPUS_REFUSAL,
)
from agents.tutor.query_resolution import definition_head, topical_words
from core.config import TUTOR_GUARD

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
RULE_FRAGMENT = "fragment"
RULE_NON_ANSWER_STATEMENT = "non_answer_statement"
RULE_SOURCE_REFERENCE = "source_reference"
RULE_CLAIM_QUESTION = "claim_question"
RULE_PRONOUN_STATEMENT = "pronoun_statement"
RULE_BACK_REFERENCE = "back_reference"
RULE_FORMULA = "formula"
RULE_FALLBACK_KEPT_TEXT = "fallback_kept_text"
RULE_VERDICT_REASON = "verdict_reason"
RULE_COURSE_CLAIM = "course_claim"
RULE_DEFINITION_EXTRA = "definition_extra"
RULE_REPEATED_QUESTION = "repeated_question"
RULE_COURSE_GAP = "course_gap"
RULE_PROGRESS_CLAIM = "progress_claim"
RULE_RESTATED_CORRECTION = "restated_correction"

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
    definition_allowed: bool = False  # a pure "what is X?" opening, not graded work
    previous_tutor_message: str | None = None  # what the tutor said last (hint and reply paths)
    student_messages: tuple[str, ...] = ()     # their earlier messages in the conversation window


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
    return [part for sentence in sentences for part in _split_after_formula(sentence)]


def _split_after_formula(sentence: str) -> list[str]:
    """
    "R_T = R1 + R2 Take the next step: ...?" has no full stop after the
    formula, so it read as one long question. A formula followed directly by a
    capitalised word ends its sentence.
    """
    parts = []
    start = 0
    for m in _FORMULA_BREAK_RE.finditer(sentence):
        parts.append(sentence[start:m.end()].strip())
        start = m.end()
    parts.append(sentence[start:].strip())
    return [p for p in parts if p]


@dataclass
class _Line:
    marker: str
    sentences: list[str] = field(default_factory=list)
    in_code: bool = False  # inside a fenced code block, or a fence line itself


def _parse(text: str) -> list[_Line | None]:
    """Lines of the reply; None stands for a blank line (a paragraph break)."""
    lines: list[_Line | None] = []
    in_fence = False
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append(None)
            continue
        fence = raw.lstrip().startswith("```")
        m = _MARKER_RE.match(raw)
        marker = m.group(1) if m else ""
        line = _Line(marker=marker, sentences=_split_sentences(raw[len(marker):]))
        line.in_code = in_fence or fence
        if fence:
            in_fence = not in_fence
        lines.append(line)
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


# ── 1a. Progress they have not made ──────────────────────────────────────────
#
# Seen live: Hint 1 opened "Now that you have the table size set up" right
# after the student got the size wrong, and the next hint "Now that you are
# reading the matrix dimensions into your array" when they had written no
# code. A claim that they have done or got something is kept only when their
# own messages (the reply and the earlier ones in the window) carry it.

_PROGRESS_VERBS = (
    r"set\s+up|setting\s+up|built|building|got|found|worked\s+out|working\s+out|filled(?:\s+in)?|"
    r"filling(?:\s+in)?|chosen|choosing|written|writing|read|reading|figured\s+out|figuring\s+out|"
    r"established|defined|defining|initiali[sz]ed|created|creating|completed|done|finished|identified|"
    r"identifying|determined|understood|grasped|learn(?:ed|t)|sorted|computed|calculated|placed|put|"
    r"decided|picked|traced|drawn|drawing|listed|noted"
)
_PROGRESS_CLAIM_RE = re.compile(
    r"\byou(?:'ve|\s+have|\s+had|'re|\s+are)\s+(?:already\s+|now\s+|just\s+|successfully\s+)?"
    r"(?:(?:\w+|[\w\[\]()+-]+)\s+){0,6}?(?:" + _PROGRESS_VERBS + r")\b",
    re.IGNORECASE,
)
_CLAUSE_END_RE = re.compile(r"[,;:]|\s[-\u2014\u2013]\s")


def _progress_claim(sentence: str, turn: TutorTurn, reply_words: set[str]) -> re.Match | None:
    """The unsupported progress claim in ``sentence``, if any."""
    plain = _plain(sentence)
    m = _PROGRESS_CLAIM_RE.search(plain)
    if not m:
        return None
    clause = _CLAUSE_END_RE.split(plain[m.start():], maxsplit=1)[0]
    attributed = _content_words(clause) - {"you", "already", "now", "just"}
    supported = reply_words | set().union(*(_content_words(t) for t in turn.student_messages))
    if attributed and _overlap(attributed, supported) >= 0.5:
        return None
    return m


def _without_progress_claim(sentence: str, turn: TutorTurn, reply_words: set[str]) -> str | None:
    m = _progress_claim(sentence, turn, reply_words)
    if m is None:
        return sentence
    plain = _plain(sentence)
    first_clause = _CLAUSE_END_RE.split(plain, maxsplit=1)[0]
    if m.start() < len(first_clause):
        # "Now that you have X set up, what ...?" keeps its question.
        cut = _cut_leading_clause(sentence, lambda clause: _PROGRESS_CLAIM_RE.search(_plain(clause)) is not None)
        if cut != sentence:
            return cut
    return sentence if _is_question(sentence) else None


# ── 1b. Pointing at sources they have never seen ──────────────────────────────

#: Left over after strip_source_narration has removed the forms it can rewrite.
#: A sentence still holding one of these cannot be repaired ("Can you recall the
#: definition given in the passage...?"), so it is dropped. "the text", "the
#: reading", "the section" and "the material" are not here on purpose: they are
#: only removed in their narrating forms, because in a literature course "the
#: text" can be the novel itself.
_SOURCE_REFERENCE_RE = re.compile(
    r"\b(?:the|this)\s+passages?\b"
    r"|\bthe\s+(?:excerpt|extract)\b"
    r"|\bthe\s+description\s+(?:of|given|in)\b"
    r"|\bthe\s+(?:definition|explanation)\s+given\b"
    r"|\bthe\s+provided\s+(?:text|materials?|information)\b",
    re.IGNORECASE,
)


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
    all_course_sentences: list[set[str]]  # content words of every course sentence
    term: set[str] = field(default_factory=set)  # head of a "what is X?" question's term

    @property
    def course_words(self) -> set[str]:
        return set().union(*self.all_course_sentences)


def _answer_signals(turn: TutorTurn, reply_words: set[str]) -> _AnswerSignals:
    topic = _content_words(turn.question)
    needed = min(2, len(topic))
    course_sentences = []
    all_course_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", turn.course_text or ""):
        words = _content_words(sentence)
        if words:
            all_course_sentences.append(words)
        if needed and len(words & topic) >= needed:
            course_sentences.append(words - topic)
    term = _content_words(definition_head(turn.question) or "")
    return _AnswerSignals(topic, needed, course_sentences, reply_words, all_course_sentences, term)


def _states_answer(statement: str, signals: _AnswerSignals) -> bool:
    """A statement that tells them what the question asks, in definition form or in the course's own words."""
    plain = _SUBORDINATE_RE.sub("", _plain(statement))
    words = _content_words(plain)
    # "What is the pivot in quick sort?" is answered by "The pivot is ...", which
    # names only the term, not two of the question's words.
    if signals.term and signals.term <= words and _DEFINING_RE.search(plain):
        return not (words - signals.topic and _overlap(words - signals.topic, signals.reply_words) >= 0.6)
    if not signals.needed or len(words & signals.topic) < signals.needed:
        return False
    rest = words - signals.topic
    # Confirming a correct attempt in their own words is not giving it away.
    if rest and _overlap(rest, signals.reply_words) >= 0.6:
        return False
    if _DEFINING_RE.search(plain):
        return True
    return len(rest) >= 3 and any(_overlap(rest, course) >= 0.6 for course in signals.course_sentences)


# ── 2e. Course claims below Hint 3 ────────────────────────────────────────────
#
# "To keep track of the region containing elements smaller than the pivot, a
# specific index is maintained starting just before the low boundary." names no
# definition and no pronoun, so the rules above let it through, yet it is the
# course's answer in other words. A statement (or the lead-in of a question) is
# a course claim when it shares at least claim_min_shared_words (core/config.py)
# words with a single sentence of the retrieved passages, after setting aside
# the question's own words, the student's own words and words common to most
# passages.
#
# Course text often states the claim in code ("int i = low - 1", "arr[j] <=
# pivot") while the tutor says it in words ("one position before low", "less
# than or equal to"), so common code symbols are read as words on both sides.

#: A question's first clause that itself asks ("What variable is set up before
#: the loop, and how...?") is the question, not a lead-in; cutting it would
#: leave "how does its value change?" with nothing for "its" to refer to.
_ASKS_RE = re.compile(r"^(?:what|why|how|which|who|whom|whose|where)\b", re.IGNORECASE)

_CODE_AS_WORDS = (
    (re.compile(r"<="), " less equal "),
    (re.compile(r">="), " greater equal "),
    (re.compile(r"!="), " not equal "),
    (re.compile(r"=="), " equal "),
    (re.compile(r"\+\+|\+=\s*1\b"), " increment "),
    (re.compile(r"(?<![-\w])--(?!-)|-=\s*1\b"), " decrement "),
    (re.compile(r"\b([A-Za-z_]\w*)\s*-\s*1\b"), r"\1 minus one before "),
    (re.compile(r"\b([A-Za-z_]\w*)\s*\+\s*1\b"), r"\1 plus one after "),
    (re.compile(r"(?<![<>=!-])<(?![<=])"), " less "),
    (re.compile(r"(?<![<>=!-])>(?![>=])"), " greater "),
)


def _claim_words(text: str) -> set[str]:
    """Content words, reading common code symbols as the words they stand for."""
    for pattern, words in _CODE_AS_WORDS:
        text = pattern.sub(words, text)
    return _content_words(text)


@dataclass(frozen=True)
class _ClaimSignals:
    units: list[set[str]]   # claim words of each passage sentence, minus `ignore`
    ignore: set[str]        # the question's words and background
    # The student's words. They count toward a claim, but a claim needs some
    # words of its own: "the index moves when an element is less than or equal
    # to the pivot" told someone who wrote "pivot" still states the condition,
    # while "Yes, i starts at low minus one" to someone who wrote that does not.
    student: set[str] = field(default_factory=set)


def _claim_signals(turn: TutorTurn) -> _ClaimSignals:
    units = [_claim_words(s) for s in re.split(r"(?<=[.!?])\s+|\n+", turn.course_text or "")]
    units = [u for u in units if u]
    background: set[str] = set()
    # Words in most passage sentences are background ("pivot", "array" in a
    # sorting course), not part of any one claim.
    if len(units) >= TUTOR_GUARD.claim_min_sentences_for_background:
        counts = Counter(w for u in units for w in u)
        share = TUTOR_GUARD.claim_background_share
        background = {w for w, n in counts.items() if n / len(units) > share}
    student = _claim_words(turn.student_reply or "") if turn.path == PATH_REPLY else set()
    # Their earlier messages in the window count as their words on every path.
    student |= set().union(*(_claim_words(t) for t in turn.student_messages))
    ignore = _claim_words(turn.question) | background
    return _ClaimSignals([u - ignore for u in units], ignore, student - ignore)


def _course_claim_words(text: str, claims: _ClaimSignals) -> set[str]:
    """
    The words, not the student's, that make ``text`` a course claim: what it
    shares with the passage sentence it claims. Empty when it is not one.
    """
    words = _claim_words(_SUBORDINATE_RE.sub("", _plain(text))) - claims.ignore
    if len(words) < TUTOR_GUARD.claim_min_shared_words:
        return set()
    for unit in claims.units:
        shared = words & unit
        new = shared - claims.student
        if (
            len(shared) >= TUTOR_GUARD.claim_min_shared_words
            and len(new) >= TUTOR_GUARD.claim_min_new_shared_words
        ):
            return new
    return set()


def _states_course_claim(text: str, claims: _ClaimSignals) -> bool:
    return bool(_course_claim_words(text, claims))


# ── 2g. A question that restates what was just removed ───────────────────────
#
# Seen live: the correction "the index moves right when an element is less than
# or equal to the pivot" was followed by "What happens to the elements when that
# index moves and you find an element less than or equal to the pivot?". Once
# the correction goes, the question must not carry it either. Only the words
# the cut correction shared with a passage sentence (and the student did not
# write) count, so a question is judged against this reply's own correction.
# Other removals (a definition, a course claim) are not used: their topic
# words are what any good follow-up question asks about.

_CLAUSE_BREAK_RE = re.compile(r"\s*[,;]\s*|\s+(?:and|while|so\s+that)\s+", re.IGNORECASE)


def _removed_claim_words(removals: list[tuple[str, str]], claims: _ClaimSignals) -> set[str]:
    in_course = set().union(*claims.units) if claims.units else set()
    removed: set[str] = set()
    for before, after in removals:
        gone = _claim_words(_plain(before)) - _claim_words(_plain(after))
        removed |= gone & in_course
    return removed - claims.student - claims.ignore


def _cut_restated_clause(question: str, removed: set[str], claims: _ClaimSignals) -> str | None:
    """
    The question without the trailing clauses that restate ``removed``, or
    None if that leaves nothing. What is kept may still name one word of it
    ("when the index moves"): one word is a noun, not the correction.
    """
    def restated(text: str) -> set[str]:
        return (_claim_words(_plain(text)) - claims.student - claims.ignore) & removed

    if len(restated(question)) < TUTOR_GUARD.claim_min_new_shared_words:
        return question
    body = question.rstrip().rstrip("?").rstrip()
    for m in reversed(list(_CLAUSE_BREAK_RE.finditer(body))):
        head = body[:m.start()].rstrip(" ,;")
        if len(restated(head)) < TUTOR_GUARD.claim_min_new_shared_words and len(_content_words(head)) >= 2:
            return head + "?"
    return None


# ── 2h. "this X" whose X was removed ──────────────────────────────────────────

_DETERMINER_RE = re.compile(r"\b(this|that|these|those)(\s+)([A-Za-z][\w-]*)", re.IGNORECASE)


def _fix_orphaned_determiners(
    lines: list[_Line | None], removals: list[tuple[str, str]], tripped: list[str]
) -> None:
    """
    "What happens to that index...?" after the sentence that introduced the
    index was removed: the question keeps its own noun, as "the index". Only a
    noun that was in removed text, and is not in what is left before it.
    """
    removed = set().union(*(_content_words(b) - _content_words(a) for b, a in removals))
    seen: set[str] = set()
    changed = False
    for line in lines:
        if line is None:
            continue
        for i, sentence in enumerate(line.sentences):
            out, end = [], 0
            for m in _DETERMINER_RE.finditer(sentence):
                noun = _content_words(m.group(3))
                earlier = seen | _content_words(sentence[:m.start()])
                if noun and noun & removed and not noun & earlier:
                    the = "The" if m.group(1)[0].isupper() else "the"
                    out.append(sentence[end:m.start()] + the + m.group(2) + m.group(3))
                    end = m.end()
            if out:
                changed = True
                line.sentences[i] = "".join(out) + sentence[end:]
            seen |= _content_words(line.sentences[i])
    if changed:
        tripped.append(RULE_BACK_REFERENCE)


# ── 2f. One definition sentence on a "what is X?" opening ────────────────────
#
# A pure "what is X?" question that is not from graded work opens with one
# definition sentence. Only the first sentence of the reply, only when it is a
# statement, and only when most of its words are in the retrieved passages:
# a definition from general knowledge stays under the answer rules.


def _definition_sentence(lines: list, turn: TutorTurn) -> str | None:
    if not (turn.definition_allowed and turn.path == PATH_NEW_QUESTION and turn.hint_level == 0):
        return None
    first = next((s for line in lines if line is not None for s in line.sentences), None)
    if not first or _is_question(first):
        return None
    words = _claim_words(_plain(first)) - _claim_words(turn.question)
    if not words:
        return None
    grounded = len(words & _claim_words(turn.course_text)) / len(words)
    return first if grounded >= TUTOR_GUARD.definition_grounded_share else None


# ── 5. No repeated question ───────────────────────────────────────────────────
#
# Seen live: Hint 1 asked "how do you use a chosen element to help organize the
# rest of the values around it?" right after "how does the algorithm use a
# chosen element to rearrange the other values around it?". A question that
# shares most of its words with a question in the tutor's last message is the
# same question again. The open question's own words are left out: every
# question about the topic shares them. Only near-repeats are caught.


def _previous_questions(turn: TutorTurn, topic: set[str]) -> list[set[str]]:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", turn.previous_tutor_message or "")
    words = [_content_words(_plain(s)) - topic for s in sentences if _is_question(s)]
    return [w for w in words if len(w) >= TUTOR_GUARD.repeat_min_words]


def _repeats_question(question: str, previous: list[set[str]], topic: set[str]) -> bool:
    words = _content_words(_plain(question)) - topic
    if len(words) < TUTOR_GUARD.repeat_min_words:
        return False
    return any(
        len(words & p) / min(len(words), len(p)) >= TUTOR_GUARD.repeat_overlap for p in previous
    )


def _non_answer_statement(statement: str, signals: _AnswerSignals) -> bool:
    """
    After "no" or "I don't know" below Hint 3, the stricter test: any statement
    that names the topic, or mostly repeats a course sentence, is telling them
    something about the topic. "Potential difference is related to the work..."
    passes the definition test above; it does not pass this one.
    """
    plain = _SUBORDINATE_RE.sub("", _plain(statement))
    words = _content_words(plain)
    if signals.needed and len(words & signals.topic) >= signals.needed:
        return True
    return len(words) >= 3 and any(
        _overlap(words, course) >= 0.6 for course in signals.all_course_sentences
    )


# ── 2a. The answer behind a pronoun ───────────────────────────────────────────

_PRONOUN_SUBJECT_RE = re.compile(
    r"^(?:it|this|that|they|these)"
    r"(?:'s|’s|'re|’re|\s+is|\s+are|\s+means|\s+refers\s+to|\s+represents|\s+measures)\b",
    re.IGNORECASE,
)


def _pronoun_states_answer(statement: str, previous: str | None, signals: _AnswerSignals) -> bool:
    """
    "It's the work or energy per unit charge..." straight after a sentence about
    the topic (or opening the reply) is the answer with "it" for the topic.
    Trips when what follows the verb has 3+ content words, at least half of
    them from the course, and they are not mostly the student's own words.
    """
    if _is_question(statement) or not signals.needed:
        return False
    plain = _plain(statement)
    m = _PRONOUN_SUBJECT_RE.match(plain)
    if not m:
        return False
    if previous is not None and len(_content_words(previous) & signals.topic) < signals.needed:
        return False  # "it" is probably not the topic
    complement = _content_words(plain[m.end():])
    if len(complement) < 3:
        return False  # "That's a common mix-up."
    if _overlap(complement, signals.reply_words) >= 0.6:
        return False  # restating what they said correctly
    return _overlap(complement, signals.course_words) >= 0.5


# ── After a removed answer sentence ───────────────────────────────────────────

_BACK_REFERENCE = r"(?:based\s+on\s+that(?:\s+idea)?|using\s+that\s+idea|with\s+that\s+in\s+mind)"
_TRAILING_BACK_REFERENCE_RE = re.compile(r",?\s+" + _BACK_REFERENCE + r"(?=\s*\?)", re.IGNORECASE)
_LEADING_BACK_REFERENCE_RE = re.compile(r"^" + _BACK_REFERENCE + r"\s*,\s*", re.IGNORECASE)


def _cut_back_reference(question: str) -> str:
    """ "Can you define it based on that idea?" -> "Can you define it?" """
    out = _TRAILING_BACK_REFERENCE_RE.sub("", question)
    led = _LEADING_BACK_REFERENCE_RE.sub("", out)
    return _capitalise(led) if led != out else out


# ── 2c. A yes/no question that carries the answer ─────────────────────────────
#
# "Do you think potential difference is related to the work or energy required
# to move charges between two points?" is a question in form and the answer in
# substance. The claim inside it is judged like a statement.

_YES_NO_RE = re.compile(
    r"^(?:(?:so|and|then|now|well|okay|ok)\b[,\s]*)?"
    r"(?:do|does|did|is|are|was|were|can|could|would|will|should|has|have)\s+(.*?)\?\s*$",
    re.IGNORECASE,
)
#: After "do you" and the like, only a verb of thinking introduces a claim:
#: "Can you describe..." and "Have you ever..." are requests, not claims.
_THINKING_RE = re.compile(
    r"^you\s+(?:think|know|remember|recall|agree|believe|suppose|feel|see|reckon)\s+"
    r"(?:that\s+)?(.*)$",
    re.IGNORECASE,
)
_OPEN_CLAUSE_RE = re.compile(r"^(?:what|why|how|when|where|which|who|whom|whose|if|whether)\b", re.IGNORECASE)


def _embedded_claim(question: str) -> str | None:
    """The claim a yes/no question asks them to agree with, or None if it has none."""
    m = _YES_NO_RE.match(_plain(question))
    if not m:
        return None
    rest = m.group(1).strip()
    if re.match(r"you\b", rest, re.IGNORECASE):
        thinking = _THINKING_RE.match(rest)
        if not thinking:
            return None
        rest = thinking.group(1).strip()
    # "Do you know what potential difference measures?" asks, it does not claim.
    if not rest or _OPEN_CLAUSE_RE.match(rest):
        return None
    return rest


# ── 3. A verdict after a non-answer ───────────────────────────────────────────

_VERDICT_RE = re.compile(
    r"^(?:great|good|nice|excellent|perfect|exactly|correct|right|precisely|absolutely|"
    r"wonderful|fantastic|awesome|brilliant|well\s+done|bravo|interesting|close|almost|"
    r"not\s+quite|not\s+exactly|nearly)\b"
    r"|\b(?:great|good|nice|excellent|perfect|interesting)\s+(?:job|work|thinking|try|effort|"
    r"start|question|point|answer|guess|attempt|thought|observation|instinct)\b"
    r"|\b(?:on\s+the\s+right\s+track|getting\s+(?:there|closer|warmer)|almost\s+there|nearly\s+there)\b"
    r"|\byou(?:'re|\s+are|\s+were)\s+(?:right|correct|close|wrong|incorrect|mistaken)\b"
    r"|\b(?:partly|partially|half|somewhat|mostly)\s+(?:right|correct)\b"
    r"|\b(?:that'?s|that\s+is|it'?s|it\s+is|this\s+is)\s+(?:not\s+)?(?:quite\s+)?"
    r"(?:right|correct|incorrect|wrong|close|true|a\s+good|an\s+interesting|a\s+great)\b"
    r"|\bnot\s+quite\b"
    r"|\byou\s+(?:didn'?t|did\s+not|haven'?t|have\s+not)\s+(?:quite\s+)?(?:answer|get|got)\b",
    re.IGNORECASE,
)


def _is_verdict(text: str) -> bool:
    return bool(_VERDICT_RE.search(_plain(text).lstrip(" \"'")))


# ── 3b. A verdict keeps its words, not its reason ─────────────────────────────
#
# "That is not quite right, because it does not start at 0 and it moves when an
# element is less than or equal to the pivot." The verdict is useful feedback on
# a real attempt; the reason after it was the answer. Below Hint 3 the reason
# clause goes and the verdict stays.
#
# Seen live as well: "You are partly right about what happens, but the index
# moves right when an element is less than or equal to the pivot, not when it
# is bigger." The ", but" clause is the reason too. And a reason that negates
# their own words ("not when it is bigger") says the attempt contradicts the
# course, so "partly right" would mislead: it becomes "not quite right".

_REASON_SPLIT_RE = re.compile(
    r"\s*(?:,\s*(?:because|since|as|but)\b|;|\s[—–-]\s|:)\s*", re.IGNORECASE
)
_PARTIAL_VERDICT_RE = re.compile(r"\b(?:partly|partially|half|somewhat|mostly)\s+(?:right|correct)\b",
                                 re.IGNORECASE)
#: A negation and what follows it, up to the end of its clause.
_NEGATED_RE = re.compile(r"(?:\bnot|n't|\bnever|\brather\s+than|\binstead\s+of)\b([^,;.!?]*)", re.IGNORECASE)
#: Said instead of "partly right" when the attempt contradicts the course.
NOT_QUITE_VERDICT = "That's not quite right."
#: A verdict that credits part of the attempt, as opposed to a correction.
_NEAR_MISS_RE = re.compile(
    r"^(?:close|nearly|almost|good\s+start|(?:partly|partially|mostly|half)\s+(?:right|correct)|"
    r"(?:that's|that\s+is|you're|you\s+are)\s+(?:close|nearly\s+there|almost\s+there|"
    r"(?:partly|partially|mostly)\s+(?:right|correct)|on\s+the\s+right\s+track))\b",
    re.IGNORECASE,
)


def _negates(reason: str, reply_words: set[str]) -> bool:
    """The reason says "not <something they wrote>": their attempt is contradicted."""
    return any(_content_words(m.group(1)) & reply_words for m in _NEGATED_RE.finditer(_plain(reason)))


def _reason_clause(sentence: str) -> str:
    """The reason after a verdict (what _cut_reason removes), or empty."""
    parts = _REASON_SPLIT_RE.split(sentence, maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _cut_reason(sentence: str, reply_words: set[str] = frozenset()) -> str:
    """The verdict alone, when the sentence is a verdict followed by its reason."""
    if _is_question(sentence):
        return sentence
    parts = _REASON_SPLIT_RE.split(sentence, maxsplit=1)
    if len(parts) < 2 or not parts[1].strip() or not _is_verdict(parts[0]):
        return sentence
    if _PARTIAL_VERDICT_RE.search(_plain(parts[0])) and _negates(parts[1], reply_words):
        return NOT_QUITE_VERDICT
    # Behind a near-miss verdict, a reason in their own words names the part
    # they got right and gives nothing away: "Close: the size does depend on
    # the knapsack size and the number of items" stays. A correction's reason
    # is the answer even when most of its words are theirs ("not quite right,
    # because it moves when an element is SMALLER than the pivot"), so only a
    # near-miss verdict keeps one.
    reason = _content_words(_plain(parts[1]))
    if _NEAR_MISS_RE.match(_plain(parts[0]).lstrip(" \"'")) and reason and _overlap(reason, reply_words) >= 0.6:
        return sentence
    return parts[0].rstrip(" ,;:.!") + "."


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


# ── 2d. Formulas (hint levels 0-2) ────────────────────────────────────────────
#
# "R_T = R1 + R2" is the answer to "what is the formula for resistors in
# series?" just as much as a sentence is. A formula here is an =, ≈ or ∝ with
# symbols on both sides and at least two distinct symbols, so "R1 = 2 Ω" (a
# given) and "2 + 3 = 5" (arithmetic) are not formulas.

_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
#: One term of a formula: a symbol (R, R1, R₁, R_T), a short capital product
#: such as "IR", a number, or an operator. A letter followed by a lowercase
#: letter is a word, not a symbol.
_TERM = (
    r"(?:[A-Z]{2,3}(?![A-Za-z])"
    r"|[A-Za-z](?:_[A-Za-z0-9]+|[0-9]+|[₀-₉]+)?(?![a-z])"
    r"|\d+(?:[.,]\d+)?"
    r"|[()+\-−*/×·÷√^²³])"
)
_SIDE = r"(?:" + _TERM + r"[ \t]*)+"
_FORMULA_RE = re.compile(r"(?<![A-Za-z0-9_])(" + _SIDE + r")(?:=|≈|∝)[ \t]*(" + _SIDE + r")")
#: A formula followed straight away by a capitalised word: the sentence break.
_FORMULA_BREAK_RE = re.compile(
    r"(?:=|≈|∝)(?:[ \t]*" + _TERM + r")+(?=[ \t]+[A-Z][a-z])"
)
_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z]{2,3}|[A-Za-z](?:_[A-Za-z0-9]+|[0-9]+|[₀-₉]+)?)"
    r"(?![A-Za-z0-9_₀-₉])"
)
_CODE_SPAN_RE = re.compile(r"`[^`]*`")


def _symbols(expr: str, split_products: bool = True) -> set[str]:
    """Symbols in an expression, normalised: R_T -> rt, R₁ -> r1, "IR" -> i, r."""
    out: set[str] = set()
    for token in _SYMBOL_RE.findall(expr.translate(_SUBSCRIPT_DIGITS)):
        if len(token) > 1 and token.isupper() and token.isalpha():
            if split_products:
                out.update(c.lower() for c in token)
            continue
        out.add(token.replace("_", "").lower())
    return out


def _formulas(text: str) -> list[tuple[set[str], set[str]]]:
    """(all symbols, right-hand-side symbols) for each formula in ``text``; code is ignored."""
    found = []
    for m in _FORMULA_RE.finditer(_CODE_SPAN_RE.sub(" ", text).replace("*", " ")):
        lhs, rhs = _symbols(m.group(1)), _symbols(m.group(2))
        if lhs and rhs and len(lhs | rhs) >= 2:
            found.append((lhs | rhs, rhs))
    return found


def _course_symbols(course_text: str) -> set[str]:
    """Symbols the course uses: those in its formulas, plus symbol-like tokens anywhere."""
    from_formulas = set().union(*(syms for syms, _ in _formulas(course_text)))
    # Single symbols in running text; "a" is the article, and all-caps words
    # such as "AC" are not split outside a formula.
    loose = _symbols(course_text, split_products=False) - {"a"}
    return from_formulas | loose


def _gives_formula(
    sentence: str,
    intro: str | None,
    signals: _AnswerSignals,
    course_symbols: set[str],
    student_symbols: set[str],
) -> bool:
    """
    True for a formula from the course, or one in a sentence (or intro line)
    about the topic -- unless the student wrote it themselves.
    """
    names_topic = any(
        text and signals.needed and len(_content_words(text) & signals.topic) >= signals.needed
        for text in (sentence, intro)
    )
    for syms, rhs in _formulas(sentence):
        if syms <= student_symbols or (len(rhs) >= 2 and rhs <= student_symbols):
            continue  # they wrote it
        if len(syms & course_symbols) >= 2 or names_topic:
            return True
    return False


def _drop_formulas(
    lines: list[_Line | None], turn: TutorTurn, signals: _AnswerSignals, tripped: list[str]
) -> None:
    """
    Remove each sentence that gives a formula away. A formula alone on its
    line also takes the intro sentence ending in ":" on the line before it
    ("the equivalent resistance R_T is:"), which would otherwise dangle.
    Fenced code is never touched.
    """
    course_symbols = _course_symbols(turn.course_text)
    student_symbols = _symbols(f"{turn.question} {turn.student_reply or ''}")
    removed = cut_back = pending = False
    previous_line: _Line | None = None
    for line in lines:
        if line is None:
            continue
        if line.in_code:
            previous_line = line
            continue
        intro = None
        if previous_line is not None and previous_line.sentences and not previous_line.in_code:
            last = previous_line.sentences[-1]
            if _plain(last).endswith(":"):
                intro = last
        alone = len(line.sentences) == 1
        kept = []
        for sentence in line.sentences:
            if _gives_formula(sentence, intro if alone else None, signals, course_symbols, student_symbols):
                removed = pending = True
                continue
            if pending and _is_question(sentence):
                trimmed = _cut_back_reference(sentence)
                cut_back = cut_back or trimmed != sentence
                sentence = trimmed
            pending = False
            kept.append(sentence)
        if alone and not kept and intro is not None:
            previous_line.sentences.pop()
        line.sentences = kept
        previous_line = line
    if removed:
        tripped.append(RULE_FORMULA)
    if cut_back:
        tripped.append(RULE_BACK_REFERENCE)


# ── Repairing joins ───────────────────────────────────────────────────────────
#
# A removal upstream (the context sanitiser) or here can leave debris where two
# sentences meet: "difference., potential difference is...". The tidy pass runs
# before the rules, so they see real sentences, and again after them.

_STRAY_BEFORE_END_RE = re.compile(r"[ \t]*[,;:]+[ \t]*(?=[.!?])")  # "repel,." -> "repel."
_REPEATED_RE = re.compile(r"([,;:])(?:[ \t]*[,;:])+")                # ", ," -> ","
_SPACE_BEFORE_RE = re.compile(r"[ \t]+([.,;:!?])")
#: A sentence end, or the start of the reply, followed by stray , ; or :
_DEBRIS_RE = re.compile(r"(^|[.!?])[ \t]*[,;:]+[ \t]*")
#: The orphaned text after the debris, up to its own sentence end.
_ORPHAN_RE = re.compile(r"[^.!?\n]*(?:[.!?]+[\"'”’)\]*_]*)?")
_CONNECTIVES = frozenset({
    "which", "that", "and", "but", "or", "so", "because", "while", "whereas", "where",
})


def _capitalise_first_word(text: str) -> str:
    """Capitalise an ordinary lowercase word; leave "x", "pH" and "iPhone" alone."""
    m = re.search(r"[A-Za-z]+", text)
    if m and len(m.group(0)) > 1 and m.group(0).islower():
        return text[:m.start()] + m.group(0).capitalize() + text[m.end():]
    return text


def _tidy(text: str) -> tuple[str, bool]:
    """
    Return the text with broken joins repaired, and whether an orphaned
    fragment had to be dropped because it could not be made a sentence.
    """
    out = _STRAY_BEFORE_END_RE.sub("", text)
    out = _REPEATED_RE.sub(r"\1", out)
    out = _SPACE_BEFORE_RE.sub(r"\1", out)
    dropped = False
    pos = 0
    while m := _DEBRIS_RE.search(out, pos):
        before = out[:m.start()].split()
        if m.group(1) == "." and before and before[-1].lower().rstrip(".") in _ABBREVIATIONS:
            pos = m.end()  # "e.g., voltage" is ordinary punctuation
            continue
        head = out[:m.start()] + m.group(1)
        orphan = _ORPHAN_RE.match(out, m.end())
        words = orphan.group(0).split()
        first = re.sub(r"[^a-z']", "", words[0].lower()) if words else ""
        if len(words) < 3 or first in _CONNECTIVES:
            mid = ""
            dropped = True
        else:
            mid = _capitalise_first_word(orphan.group(0))
        rest = out[orphan.end():]
        joiner = " " if head and mid else ""
        out = head + joiner + mid + rest
        pos = len(head) + len(joiner) + len(mid)
    return re.sub(r"[ \t]{2,}", " ", out).strip(), dropped


# ── A reply that only says the course does not cover it ──────────────────────
#
# Seen live: "This course doesn't cover that part." as a whole reply, as a
# full explanation, and glued to the fallback question. The gate had found
# the topic in the course each time. The node asks once more and then shows a
# fixed message; the guard never keeps such a sentence as an opening.

_COURSE_GAP_RE = re.compile(
    r"\b(?:this|the|our)\s+course(?:\s+material)?\s+(?:doesn't|does\s+not|didn't|did\s+not|"
    r"won't|will\s+not|can't|cannot)\s+(?:cover|explain|address|include)"
    r"|\b(?:not|isn't|aren't)\s+(?:covered|included|addressed)\s+(?:in|by)\s+(?:this|the)\s+course\b"
    # Missed live on 2026-10-12, shown as ordinary turns: "there is no mention or
    # explanation of the travelling salesman problem" and "not explained in the
    # provided material".
    r"|\bthere(?:\s+is|'s)\s+no\s+(?:mention|explanation|information|discussion|coverage)"
    r"(?:\s+or\s+\w+)?\s+of\b"
    r"|\b(?:not|isn't|aren't)\s+(?:explained|covered|mentioned|discussed|described|included|addressed)"
    r"\s+in\s+(?:this|the)\s+(?:provided\s+)?(?:course\s+)?(?:materials?|passages?|text|notes)\b",
    re.IGNORECASE,
)

#: Lead-ins that only soften a gap sentence: "I must inform you that this course
#: does not cover how binary search works" names three topic words, not four.
_GAP_FILLER = frozenset({"inform", "sorry", "unfortunately", "afraid", "provided"})


def _gap_extra_words(sentence: str) -> int | None:
    """Content words beyond the "course doesn't cover" phrase, or None without one."""
    plain = _plain(sentence)
    m = _COURSE_GAP_RE.search(plain)
    return len(_content_words(plain[: m.start()] + plain[m.end():]) - _GAP_FILLER) if m else None


def _empty_gap_sentence(sentence: str) -> bool:
    """
    "This course doesn't cover that part." and nothing more: it names no part,
    so it only contradicts the course check. "The course doesn't cover the C++
    template syntax." names what is missing, as the retry asks, and stays.
    """
    extra = _gap_extra_words(sentence)
    return extra is not None and extra <= 1


def is_course_gap_reply(text: str | None) -> bool:
    """
    The reply says the course does not cover it and nothing else of substance:
    at most one other sentence, and that one a question ("Which topic would
    you like instead?"). An explanation that mentions a gap in passing is not
    one.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]

    def bare_gap(sentence: str) -> bool:
        # "...doesn't cover recursion in general, but it does show the base
        # case: ..." says the gap and then teaches; only a sentence with
        # nothing much beyond the gap phrase counts.
        extra = _gap_extra_words(sentence)
        return extra is not None and extra <= 3

    gap = [s for s in sentences if bare_gap(s)]
    others = [s for s in sentences if s not in gap]
    return bool(gap) and len(others) <= 1 and all(_is_question(s) for s in others)


def _letters_only(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


#: The first two sentences of the out-of-corpus refusal, for spotting a copy.
_REFUSAL_SENTENCES = tuple(
    _letters_only(s) for s in re.split(r"(?<=[.!?])\s+", OUT_OF_CORPUS_REFUSAL) if s.strip()
)[:2]


def is_out_of_corpus_echo(text: str | None) -> bool:
    """
    The reply repeats the stock out-of-corpus refusal (either of its first two
    sentences, ignoring case and punctuation). Seen live: the model copied it
    from an earlier refusal in the conversation window as its answer to a
    question the gate had passed. A reply opening with a refusal marker from
    that window ("(declined: outside the course)") is a copy too.
    """
    if (text or "").lstrip().lower().startswith("(declined"):
        return True
    plain = _letters_only(text or "")
    return bool(plain) and any(s in plain for s in _REFUSAL_SENTENCES)


# ── Keeping the opening when the fallback fires ───────────────────────────────

#: At most this many sentences are kept ahead of the fallback question.
_MAX_KEPT_SENTENCES = 2


def _sentences(lines: list[_Line | None]) -> list[str]:
    return [s for line in lines if line is not None for s in line.sentences]


def _kept_opening(original: list[str], surviving: list[str]) -> list[str]:
    """
    The reply's opening sentences that every rule left exactly as they were,
    up to the first one a rule removed or changed: so nothing kept can follow,
    or point back at, a removed sentence. At most _MAX_KEPT_SENTENCES, never a
    question, and it stops before an intro line ending in ":", which would
    point at text that is no longer there.
    """
    kept: list[str] = []
    for before, after in zip(original, surviving, strict=False):
        plain = _plain(after)
        if after != before or _is_question(after) or plain.endswith(":") or _COURSE_GAP_RE.search(plain):
            break
        kept.append(after)
        if len(kept) == _MAX_KEPT_SENTENCES:
            break
    return kept


# ── The guard ─────────────────────────────────────────────────────────────────


def guard_tutor_reply(text: str, turn: TutorTurn) -> str:
    """Apply the checks and the one-question rule to a sanitised reply; log each rule that tripped."""
    out, tripped = apply_guards(text, turn)
    for rule in tripped:
        logger.info("[TUTOR-GUARD] rule=%s path=%s hint_level=%s", rule, turn.path, turn.hint_level)
    return out


def apply_guards(text: str, turn: TutorTurn) -> tuple[str, list[str]]:
    """Return the guarded reply and the names of the rules that changed it."""
    text, dropped = _tidy(text or "")
    lines = _parse(text)
    tripped: list[str] = [RULE_FRAGMENT] if dropped else []
    reply_words = _content_words(turn.student_reply or "") if turn.path == PATH_REPLY else set()
    student_numbers = _numbers(turn.question) | _numbers(turn.student_reply or "")
    course_numbers = _numbers(turn.course_text) - student_numbers
    low_rung = turn.hint_level <= MAX_ANSWER_GUARD_LEVEL
    signals = _answer_signals(turn, reply_words) if low_rung else None

    # (rule, before, after) for every sentence an answer rule removed or shortened.
    removals: list[tuple[str, str, str]] = []

    def run(rule: str, fix, *, answer: bool = False, with_previous: bool = False) -> None:
        """
        Apply ``fix`` to every sentence in order. ``with_previous`` also passes
        the sentence before it (as it stood before this pass, None for the
        first). ``answer`` marks a pass that removes answer sentences: the
        question right after a removed one loses "based on that idea" and the
        like, which would otherwise point at nothing.
        """
        changed = cut_back = pending = False
        previous = None
        for line in lines:
            if line is None:
                continue
            kept = []
            for sentence in line.sentences:
                new = fix(sentence, previous) if with_previous else fix(sentence)
                previous = sentence
                if new != sentence:
                    changed = True
                    if answer:
                        removals.append((rule, sentence, new or ""))
                if not new:
                    pending = pending or answer
                    continue
                if pending and _is_question(new):
                    trimmed = _cut_back_reference(new)
                    cut_back = cut_back or trimmed != new
                    new = trimmed
                pending = False
                kept.append(new)
            line.sentences = kept
        if changed:
            tripped.append(rule)
        if cut_back:
            tripped.append(RULE_BACK_REFERENCE)

    # 3b. On a reply to a real attempt below Hint 3, a verdict keeps its words
    # and loses its reason ("That is not quite right, because <the answer>").
    if low_rung and turn.path == PATH_REPLY and not turn.is_non_answer:
        run(RULE_VERDICT_REASON, lambda s: _cut_reason(s, reply_words), answer=True)

    # Taken after the verdict cut, so a shortened verdict still counts as the
    # untouched opening the fallback may keep.
    original = _sentences(lines)

    # 2f. The one definition sentence a "what is X?" opening may give is spared
    # by the answer rules; any later one is not.
    definition = _definition_sentence(lines, turn)

    def spare_definition(fix):
        if definition is None:
            return fix
        spared = False

        def fix_others(s, *previous):
            nonlocal spared
            if not spared and s == definition:
                spared = True
                return s
            return fix(s, *previous)

        return fix_others

    # 1. Only what they actually said.
    run(RULE_INVENTED_REFERENCE, lambda s: None if _invented_reference(s, turn, reply_words) else s)

    # 0. No empty "this course doesn't cover that part" glued to the rest of
    # a reply: the course check has just found the topic. (A reply that is
    # only that is asked again in agents/tutor/nodes.py.)
    if any(not _empty_gap_sentence(s) for s in _sentences(lines)):
        run(RULE_COURSE_GAP, lambda s: None if _empty_gap_sentence(s) else s)

    # 1a. No progress they have not made.
    run(RULE_PROGRESS_CLAIM, lambda s: _without_progress_claim(s, turn, reply_words))

    # 1b. No pointing at passages they have never seen.
    run(RULE_SOURCE_REFERENCE, lambda s: None if _SOURCE_REFERENCE_RE.search(_plain(s)) else s)

    # 2. Ask, do not tell -- below Hint 3 only.
    if signals is not None:
        def no_answer(s: str) -> str | None:
            if not _is_question(s):
                return None if _states_answer(s, signals) else s
            return _cut_leading_clause(s, lambda clause: _states_answer(clause, signals))

        run(RULE_STATES_ANSWER, spare_definition(no_answer), answer=True)

        # 2a. The same answer behind a pronoun: "It's the work or energy per
        # unit charge..." right after a sentence about the topic.
        run(
            RULE_PRONOUN_STATEMENT,
            spare_definition(
                lambda s, previous: None if _pronoun_states_answer(s, previous, signals) else s
            ),
            answer=True,
            with_previous=True,
        )

        # 2d. No formula from the course, or about the topic, below Hint 3.
        _drop_formulas(lines, turn, signals, tripped)

        # 2b. After a non-answer the tutor only asks a smaller question: no
        # statement about the topic at all, however it is worded.
        if turn.is_non_answer:
            def only_ask(s: str) -> str | None:
                if not _is_question(s):
                    return None if _non_answer_statement(s, signals) else s
                return _cut_leading_clause(s, lambda clause: _non_answer_statement(clause, signals))

            run(RULE_NON_ANSWER_STATEMENT, only_ask, answer=True)

        # 2c. A yes/no question whose claim gives the answer away: after a
        # non-answer below Hint 3 judged as strictly as a statement, and on the
        # opening question of a new ladder by the milder definition test. Hints
        # are left alone: a hint is meant to nudge.
        claim_test = None
        if turn.is_non_answer:
            claim_test = _non_answer_statement
        elif turn.path == PATH_NEW_QUESTION and turn.hint_level == 0:
            claim_test = _states_answer
        if claim_test is not None:
            def no_claim(s: str) -> str | None:
                claim = _embedded_claim(s) if _is_question(s) else None
                return None if claim and claim_test(claim, signals) else s

            run(RULE_CLAIM_QUESTION, no_claim)

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

    # 2e. Last of the answer rules, as a backstop for what the specific rules
    # above do not recognise: no course claim in other words below Hint 3. A
    # statement, or a question's lead-in, that shares several words with one
    # passage sentence.
    if low_rung:
        claims = _claim_signals(turn)

        def no_claim_statement(s: str) -> str | None:
            if not _is_question(s):
                return None if _states_course_claim(s, claims) else s
            return _cut_leading_clause(
                s,
                lambda clause: not _ASKS_RE.match(_plain(clause).lstrip())
                and _states_course_claim(clause, claims),
            )

        run(RULE_COURSE_CLAIM, spare_definition(no_claim_statement), answer=True)

        # 2g. A question that says again the correction just cut from a verdict
        # ("...when you find an element less than or equal to the pivot?")
        # loses that clause, or goes when the clause cannot be cut.
        corrections = [(_reason_clause(b), "") for rule, b, _ in removals if rule == RULE_VERDICT_REASON]
        removed_claim = _removed_claim_words(corrections, claims)
        if len(removed_claim) >= TUTOR_GUARD.claim_min_new_shared_words:
            run(
                RULE_RESTATED_CORRECTION,
                lambda s: _cut_restated_clause(s, removed_claim, claims) if _is_question(s) else s,
                answer=True,
            )

    # 2h. "this index" or "that boundary index" whose noun was only in a
    # removed sentence or clause now points at nothing: it becomes "the index".
    if removals:
        _fix_orphaned_determiners(lines, [(b, a) for _, b, a in removals], tripped)

    # 2f. A definition opening is the one sentence and a question: any other
    # statement, however short ("It is measured in volts."), is a second fact.
    if definition is not None:
        run(
            RULE_DEFINITION_EXTRA,
            spare_definition(lambda s: s if _is_question(s) else None),
            answer=True,
        )

    # 5. No question asked again in other words, on a hint or after "no" / "I
    # don't know". A reply to an attempt or to an off-topic message may ask
    # the same question again on purpose, to steer back to it. The full
    # explanation asks nothing, so it is left alone.
    topic = _content_words(turn.question)
    asks_again = turn.path == PATH_HINT or (turn.path == PATH_REPLY and turn.is_non_answer)
    previous = _previous_questions(turn, topic) if asks_again else []
    if previous and turn.hint_level < _FULL_EXPLANATION_LEVEL:
        run(
            RULE_REPEATED_QUESTION,
            lambda s: None if _is_question(s) and _repeats_question(s, previous, topic) else s,
        )

    # Repair any join the removals above left, then judge what is left.
    text, dropped = _tidy(_render(lines))
    if dropped and RULE_FRAGMENT not in tripped:
        tripped.append(RULE_FRAGMENT)
    lines = _parse(text)

    if not text or (
        tripped
        and (low_rung or turn.path == PATH_REPLY)
        and not any(_is_question(s) for line in lines if line for s in line.sentences)
    ):
        # A reply to a real attempt below Hint 3 keeps what it had already got
        # right ("That's not quite right.") and gains the fallback question,
        # instead of losing the verdict along with the removed sentence. A
        # hint that lost only its repeated question keeps its new pointer too.
        hint_lost_repeat = turn.path == PATH_HINT and RULE_REPEATED_QUESTION in tripped
        if low_rung and not turn.is_non_answer and (turn.path == PATH_REPLY or hint_lost_repeat):
            opening = _kept_opening(original, _sentences(lines))
            if opening:
                tripped.append(RULE_FALLBACK_KEPT_TEXT)
                return " ".join(opening) + " " + GUARD_FALLBACK_QUESTION, tripped
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
