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
RULE_FRAGMENT = "fragment"
RULE_NON_ANSWER_STATEMENT = "non_answer_statement"
RULE_SOURCE_REFERENCE = "source_reference"
RULE_CLAIM_QUESTION = "claim_question"
RULE_PRONOUN_STATEMENT = "pronoun_statement"
RULE_BACK_REFERENCE = "back_reference"
RULE_FORMULA = "formula"
RULE_FALLBACK_KEPT_TEXT = "fallback_kept_text"

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
    return _AnswerSignals(topic, needed, course_sentences, reply_words, all_course_sentences)


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
        if after != before or _is_question(after) or plain.endswith(":"):
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
    original = _sentences(lines)  # before any rule, to find the untouched opening
    tripped: list[str] = [RULE_FRAGMENT] if dropped else []
    reply_words = _content_words(turn.student_reply or "") if turn.path == PATH_REPLY else set()
    student_numbers = _numbers(turn.question) | _numbers(turn.student_reply or "")
    course_numbers = _numbers(turn.course_text) - student_numbers
    low_rung = turn.hint_level <= MAX_ANSWER_GUARD_LEVEL
    signals = _answer_signals(turn, reply_words) if low_rung else None

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

    # 1. Only what they actually said.
    run(RULE_INVENTED_REFERENCE, lambda s: None if _invented_reference(s, turn, reply_words) else s)

    # 1b. No pointing at passages they have never seen.
    run(RULE_SOURCE_REFERENCE, lambda s: None if _SOURCE_REFERENCE_RE.search(_plain(s)) else s)

    # 2. Ask, do not tell -- below Hint 3 only.
    if signals is not None:
        def no_answer(s: str) -> str | None:
            if not _is_question(s):
                return None if _states_answer(s, signals) else s
            return _cut_leading_clause(s, lambda clause: _states_answer(clause, signals))

        run(RULE_STATES_ANSWER, no_answer, answer=True)

        # 2a. The same answer behind a pronoun: "It's the work or energy per
        # unit charge..." right after a sentence about the topic.
        run(
            RULE_PRONOUN_STATEMENT,
            lambda s, previous: None if _pronoun_states_answer(s, previous, signals) else s,
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
        # instead of losing the verdict along with the removed sentence.
        if low_rung and turn.path == PATH_REPLY and not turn.is_non_answer:
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
