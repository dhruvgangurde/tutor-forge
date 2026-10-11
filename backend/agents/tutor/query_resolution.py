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


# ── Turn classification ───────────────────────────────────────────────────────
#
# A student message mid-conversation is usually not a new question. It is an
# answer to the tutor's own question ("no", "I don't know", "because they have
# the same charge?") or a short follow-up ("why?", "what about the worst case").
# Judging those on their own text refused them: "umm, work is required to move
# something" has plenty of words but scores below the bar, and "no" has none.
# So each message is classified first, by fixed rules (no model call), and only
# a NEW question is judged on its own text. Everything else is judged against
# the open learning question.
#
# Residual risk, accepted: an off-topic message that is not phrased as a
# question ("sourdough bread") inside an open conversation is answered in that
# conversation, from its course chunks only, with an instruction to ignore the
# off-topic part and steer back. Retrieval scores cannot separate these cases
# (see the measurements above).

TURN_NEW_QUESTION = "new_question"
TURN_FOLLOW_UP = "follow_up"
TURN_REPLY = "reply"

#: First words that make a message a question or a request. Includes the
#: imperative openers ("tell me about…", "explain…") and the auxiliaries that
#: open yes/no questions, so "tell me about sourdough bread" is a new question
#: and is judged — and refused — on its own text.
_QUESTION_OPENERS = frozenset({
    "what", "whats", "what's", "why", "how", "who", "whom", "whose", "when",
    "where", "which",
    "tell", "explain", "define", "describe", "show", "give", "list", "solve",
    "calculate", "derive", "compare",
    "is", "are", "does", "do", "can", "could",
})

#: Skipped at the start of a message before looking for an opener.
_INTERJECTIONS = frozenset({
    "umm", "um", "uh", "uhh", "hmm", "hm", "ok", "okay", "well", "oh", "hey",
    "hi", "so", "and", "please", "sorry",
})

#: Openers that ask about the conversation already under way, not a new topic.
_FOLLOW_UP_PREFIXES = ("what about", "how about", "what if", "why not", "what else", "how come")

#: A pronoun right after the opener points back at the conversation:
#: "is it because…", "does that mean…", "how does it…".
_REFERRING_WORDS = frozenset({
    "it", "its", "it's", "that", "that's", "this", "they", "them", "these",
    "those", "there",
})


def _leading_words(text: str) -> list[str]:
    words = _WORD_RE.findall((text or "").lower())
    while words and words[0] in _INTERJECTIONS:
        words.pop(0)
    return words


def classify_turn(text: str) -> str:
    """
    Classify a student message as a new question, a follow-up, or a reply.

    - new question: starts with a question word or request verb and names a
      topic of its own ("what is potential difference?", "tell me about pizza").
    - follow-up: a question about the conversation already under way ("why?",
      "what about the worst case", "is it because of the charge?", "give me the
      answer").
    - reply: anything else, i.e. an answer or a non-answer to the tutor's own
      question ("no", "I don't know", "because they have same charge?").

    The caller treats any message as a new question when there is no open
    learning question yet.
    """
    words = _leading_words(text)
    if not words or words[0] not in _QUESTION_OPENERS:
        return TURN_REPLY
    joined = " ".join(words)
    if any(joined.startswith(prefix) for prefix in _FOLLOW_UP_PREFIXES):
        return TURN_FOLLOW_UP
    if any(w in _REFERRING_WORDS for w in words[1:3]):
        return TURN_FOLLOW_UP
    if not topical_words(joined):
        return TURN_FOLLOW_UP
    return TURN_NEW_QUESTION


#: Replies that say "I can't answer that" rather than attempting an answer.
#: Matched on the normalised message, exactly or as a prefix.
_NON_ANSWER_EXACT = frozenset({
    "no", "nope", "nah", "not really", "maybe", "umm", "um", "hmm", "idk",
    "dunno", "i dunno", "no idea", "no clue", "not sure", "i forgot",
})
_NON_ANSWER_PREFIXES = (
    "i dont know", "i don't know", "dont know", "don't know", "i do not know",
    "idk", "no idea", "not sure", "im not sure", "i'm not sure", "i am not sure",
    "i have no idea", "i dont remember", "i don't remember", "i cant remember",
    "i can't remember", "im lost", "i'm lost", "im stuck", "i'm stuck",
)


def is_non_answer(text: str) -> bool:
    """True when a reply says the student doesn't know, rather than answering."""
    normalised = " ".join(_leading_words(_normalise(text)))
    if not normalised:
        return True
    if normalised in _NON_ANSWER_EXACT:
        return True
    return any(normalised.startswith(prefix) for prefix in _NON_ANSWER_PREFIXES)


# ── Query resolution ──────────────────────────────────────────────────────────

#: ``retrieval_query_source`` values recorded on the state for observability.
#: "prior_turn" means retrieval ran on the open learning question, which is an
#: earlier turn of the conversation.
SOURCE_MESSAGE = "message"
SOURCE_PRIOR_TURN = "prior_turn"


def resolve_retrieval_query(
    question: str,
    prior_question: str | None,
) -> tuple[str, str]:
    """
    Return ``(query, source)`` — the text to retrieve on, and where it came from.

    ``prior_question`` is the open learning question of the conversation, if
    there is one. Any message that is not a new question (a reply or a
    follow-up) is retrieved on it instead of on its own text. Substitution, not
    concatenation: see the module docstring for the measurement that rules
    concatenation out.

    With no open question (the first message of a session) the message is
    used as-is, so a session that opens with "give me the answer" is still
    correctly judged out-of-corpus. A new question is always judged on its own
    text, which keeps the off-topic controls refused mid-conversation.
    """
    if prior_question and classify_turn(question) != TURN_NEW_QUESTION:
        return prior_question, SOURCE_PRIOR_TURN
    return question, SOURCE_MESSAGE
