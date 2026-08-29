"""
agents/tutor/query_resolution.py
---------------------------------
Decide WHAT text to retrieve on, and whether the student is asking to skip the
pedagogy.

The problem this solves
-----------------------
``retrieve_context_node`` used to embed the raw current message and nothing
else. Mid-conversation, a student who types something contentless — "give me
the answer", "what about the worst case", "why?" — produces an embedding with
almost no topical signal, scores below the groundedness bar, and gets the
out-of-corpus refusal even though the conversation is squarely on-topic.

Measured on the live stack (nomic-embed-text, DSA course, bar 0.57):

    "What is the time complexity of binary search and why?"   0.6616  passes
    "give me the answer"                                      0.5414  REFUSED
    "what about the worst case"                               0.4979  REFUSED
    "why?"                                                    0.5082  REFUSED
    control: "best recipe for sourdough bread"                0.5108  refused
    control: "who won the 1998 football world cup"            0.3813  refused

Note that the on-topic follow-up scores *higher* than a genuinely off-topic
control. The score does not separate these classes for short messages, so no
threshold value fixes it.

Why not just concatenate the history
------------------------------------
Measured and rejected. Prepending the prior turn does clear the false refusals,
but the prior turn then dominates the embedding and every off-topic follow-up
clears the bar too:

    prior + "give me the answer"        0.6707  (wanted)
    prior + "sourdough bread recipe"    0.6565  (a genuinely off-topic pass)
    prior + "who won the 1998 world cup" 0.6444  (a genuinely off-topic pass)

So this module *substitutes* the prior turn's question rather than blending it
in, and only for messages carrying essentially no topical content of their own.
A message with real topical content is always retrieved on its own text, which
is what keeps the off-topic controls above refusing.

Known limitation, deliberately accepted: a *very short* off-topic follow-up
(two or fewer topical words, e.g. "sourdough bread?") falls under the
low-content rule and inherits the prior turn's grounding. The blast radius is
relevance, not leakage — generation still happens only against chunks retrieved
from this course, and every ladder rung keeps the "use ONLY the provided course
context" clause — so the student gets an on-course reply to an off-course
question, not general model knowledge. See tests/test_tutoring.py.
"""

from __future__ import annotations

import re

# ── Low-content detection ─────────────────────────────────────────────────────

#: Words that carry no retrievable topic on their own: function words, plus the
#: verbs and nouns students use to ask *about the interaction* rather than about
#: the subject ("tell", "answer", "explain", "hint"). Everything not in this set
#: counts as topical.
_FILLER_WORDS = frozenset({
    # articles / pronouns / conjunctions / prepositions
    "a", "an", "the", "i", "me", "my", "you", "your", "it", "its", "this",
    "that", "these", "those", "they", "them", "we", "us", "he", "she", "him",
    "her", "and", "or", "but", "so", "if", "then", "than", "as", "of", "to",
    "in", "on", "at", "for", "with", "about", "from", "by", "is", "are", "was",
    "were", "be", "been", "am", "do", "does", "did", "doing", "have", "has",
    "had", "can", "could", "will", "would", "should", "shall", "may", "might",
    "must", "not", "no", "yes", "up", "out", "more", "again", "still", "just",
    "any", "some", "all", "one", "next", "please", "ok", "okay", "thanks",
    "thank", "hi", "hey", "hello", "sorry",
    # interrogatives
    "what", "whats", "why", "how", "when", "where", "which", "who", "whom",
    # meta / interaction verbs and nouns
    "tell", "give", "show", "say", "answer", "answers", "solution", "solutions",
    "solve", "explain", "explanation", "help", "hint", "hints", "clarify",
    "understand", "know", "get", "see", "think", "mean", "means", "meaning",
    "question", "ask", "repeat", "rephrase", "elaborate", "continue", "go",
    "sure", "idea", "lost", "stuck", "confused", "thing", "stuff", "part",
    "bit", "little", "much", "many",
    # contractions: the tokenizer keeps apostrophes, so these need listing in
    # both forms or "don't" counts as a topical word.
    "dont", "don't", "cant", "can't", "im", "i'm", "its", "it's", "whats",
    "what's", "thats", "that's", "doesnt", "doesn't", "didnt", "didn't",
    "wont", "won't", "isnt", "isn't", "ive", "i've", "id", "i'd", "ill",
    "i'll", "youre", "you're", "lets", "let's",
})

#: A message needs at least this many topical words to be retrieved on its own
#: text. Calibrated against the diagnostic corpus above: "what about the worst
#: case" yields {worst, case} = 2 and must fall back, while the real question
#: "What is the time complexity of binary search and why?" yields
#: {time, complexity, binary, search} = 4 and must not. Both off-topic controls
#: yield 4+ topical words, so they keep being scored — and refused — on their own.
_MIN_TOPICAL_WORDS = 3

_WORD_RE = re.compile(r"[a-z0-9']+")


def topical_words(text: str) -> list[str]:
    """Return the words in ``text`` that carry retrievable topic signal."""
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _FILLER_WORDS]


def is_low_content(text: str) -> bool:
    """
    True when ``text`` has too little topical signal to retrieve on by itself.

    Intentionally a word-count rule rather than a model call: it must be cheap
    (it runs before every tutor turn), deterministic, and directly testable
    against the measured queries above.
    """
    return len(topical_words(text)) < _MIN_TOPICAL_WORDS


# ── Pedagogy-skip detection (FR-03.3) ─────────────────────────────────────────

#: Normalised phrases that mean "stop teaching me and hand over the answer".
#: Matched as substrings of the normalised message, so "ok just tell me the
#: answer already" hits "just tell me".
#:
#: Kept as an explicit, small list on purpose. This gate decides whether an
#: on-topic student gets tutoring or a decline, so a false positive is a real
#: cost; a curated list is auditable in a way a fuzzy classifier is not, and it
#: fails safe — anything unmatched is tutored normally.
_SKIP_PEDAGOGY_PATTERNS = (
    "give me the answer",
    "give me the solution",
    "just give me",
    "just tell me",
    "tell me the answer",
    "tell me the solution",
    "what is the answer",
    "whats the answer",
    "what's the answer",
    "show me the answer",
    "show me the solution",
    "give the answer",
    "solve it for me",
    "solve this for me",
    "do it for me",
    "answer it for me",
    "skip the questions",
    "stop asking me questions",
    "no more questions",
    "i give up",
    "just answer",
)

_PUNCT_RE = re.compile(r"[^a-z0-9\s']+")
_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for phrase matching."""
    lowered = (text or "").lower()
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", lowered)).strip()


def is_pedagogy_skip_request(text: str) -> bool:
    """
    True when the student is asking to be handed the answer outright.

    This is a *separate* question from groundedness. FR-02.7 ("is this in the
    corpus?") and FR-03.3 ("is the student trying to skip the pedagogy?") were
    collapsed into one refusal before this existed, which is why an on-topic
    student asking for the answer was told their question was off-syllabus.
    """
    normalised = _normalise(text)
    if not normalised:
        return False
    return any(pattern in normalised for pattern in _SKIP_PEDAGOGY_PATTERNS)


# ── Query resolution ──────────────────────────────────────────────────────────

#: ``retrieval_query_source`` values recorded on the state for observability.
SOURCE_MESSAGE = "message"
SOURCE_PRIOR_TURN = "prior_turn"


def resolve_retrieval_query(
    question: str,
    prior_question: str | None,
) -> tuple[str, str]:
    """
    Return ``(query, source)`` — the text to retrieve on, and where it came from.

    Substitutes the prior substantive question when the current message carries
    no topic of its own. Substitution, not concatenation: see the module
    docstring for the measurement that rules concatenation out.

    With no usable prior turn (the first message of a session) the message is
    used as-is, so a session that opens with "give me the answer" is still
    correctly judged out-of-corpus.
    """
    if prior_question and is_low_content(question):
        return prior_question, SOURCE_PRIOR_TURN
    return question, SOURCE_MESSAGE
