"""
agents/assessment/validation.py
-------------------------------
Deterministic checks on generated questions. No model call.

Seen live (Gemini, a quick sort course): a three-question quiz that was
grounded but weak. "What is the simplest choice for the pivot?" has three
defensible answers (first, middle and last are all simple); a base-case
distractor "If low == high" was arguably correct next to the key "If low >=
high"; and two of the three questions were easy recall. The prompt asked for
none of that, and nothing checked it afterwards.

Each check returns a reason code; agents/assessment/nodes.py regenerates a
failing question once and drops it if it still fails.

  unsupported_key           a recall question whose correct answer is not
                            stated in the retrieved passages
  vague_superlative         "simplest", "best"... with no stated criterion
  distractor_restates_key   a wrong option that says almost what the key says
  malformed_mcq             not four options, or no valid answer letter
"""

from __future__ import annotations

import difflib
import math
import re

from agents.tutor.query_resolution import topical_words
from core.config import QUESTION_CHECKS

REASON_UNSUPPORTED_KEY = "unsupported_key"
REASON_VAGUE_SUPERLATIVE = "vague_superlative"
REASON_DISTRACTOR_RESTATES_KEY = "distractor_restates_key"
REASON_MALFORMED_MCQ = "malformed_mcq"
REASON_NOT_HIGHER_ORDER = "not_higher_order"

#: Bloom levels that ask the student to use an idea rather than recall it.
HIGHER_ORDER_LEVELS = frozenset({"apply", "analyze", "evaluate", "create"})
#: Levels whose key must be stated in the passages; a higher-order key is
#: worked out from them, so it need not appear word for word.
_RECALL_LEVELS = frozenset({"remember", "understand"})

# The thresholds below are in core/config.py (QUESTION_CHECKS).
#
# A recall key is supported when most of its words are in one passage
# sentence, and every number in it appears in the passages.
#
# A wrong option restates the key when it has the same content words AND is
# nearly the same character by character (difflib ratio): "If low == high"
# next to "If low >= high". Either test alone is wrong: "The first element"
# is 0.8 similar to "The last element", and "Swap, then increment i" has the
# same words as "Increment i, then swap"; both are good distractors. Short
# keys ("O(n)") differ from good distractors ("O(n^2)") by too few
# characters for a ratio to mean anything, so they are not compared.

_LETTERS = "ABCD"
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
#: Tokens that must match exactly: a complexity like "O(n log n)" and any number.
_EXACT_RE = re.compile(r"\bO\s*\([^)]*\)|\d+(?:\.\d+)?", re.IGNORECASE)
_VAGUE_RE = re.compile(
    r"\b(?:simplest|easiest|best(?![\s-]+case)|better|optimal|ideal|preferred|recommended|"
    r"most\s+(?:appropriate|suitable|common|important|efficient|effective|useful|likely))\b",
    re.IGNORECASE,
)
#: A criterion that makes a superlative answerable from the course.
_CRITERION_RE = re.compile(
    r"\b(?:in\s+terms\s+of|with\s+respect\s+to|measured\s+by|as\s+measured|by\s+the\s+number\s+of|"
    r"requires?\s+the\s+(?:fewest|least)|with\s+the\s+(?:fewest|lowest|least|smallest))\b",
    re.IGNORECASE,
)

#: Shown to the model on regeneration. Fixed text: never the rejected question.
REASON_TEXT = {
    REASON_UNSUPPORTED_KEY: "its correct answer is not stated in the course content",
    REASON_VAGUE_SUPERLATIVE: (
        'its stem used a word like "simplest" or "best" without saying by what measure, '
        "so more than one option could be defended"
    ),
    REASON_DISTRACTOR_RESTATES_KEY: (
        "a wrong option said almost the same thing as the correct answer, so more than one "
        "option could be defended"
    ),
    REASON_MALFORMED_MCQ: "it did not have exactly four options and one answer letter A-D",
    REASON_NOT_HIGHER_ORDER: "it must make the student apply or analyze the idea, not recall it",
}


def _words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in topical_words(text or "")}


def evidence_sentences(texts: list[str]) -> list[str]:
    """The retrieved passages split into sentences (code without full stops stays one unit)."""
    out: list[str] = []
    for text in texts:
        out.extend(s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip())
    return out


def mcq_key_index(question: dict) -> int | None:
    """Position of the correct MCQ option, or None when the options or letter are not usable."""
    options = question.get("options")
    letter = str(question.get("correct_answer") or "").strip().upper()[:1]
    if not isinstance(options, list) or len(options) != 4 or not letter or letter not in _LETTERS:
        return None
    return _LETTERS.index(letter)


def _key_supported(key: str, evidence: list[str]) -> bool:
    numbers = set(_NUMBER_RE.findall(key))
    if numbers and not numbers <= set(_NUMBER_RE.findall(" ".join(evidence))):
        return False
    words = _words(key)
    if not words:
        return True  # nothing but numbers or symbols, already checked
    best = max((len(words & _words(s)) / len(words) for s in evidence), default=0.0)
    return best >= QUESTION_CHECKS.key_support_share


def _exact_tokens(text: str) -> list[str]:
    return [re.sub(r"\s+", "", t).lower() for t in _EXACT_RE.findall(text)]


def _restates(option: str, key: str) -> bool:
    a = " ".join(option.lower().split()).strip(" .")
    b = " ".join(key.lower().split()).strip(" .")
    if len(b) < QUESTION_CHECKS.restates_key_min_chars or _words(a) != _words(b):
        return False
    # "O(log n)" is not "O(n log n)", and 17 is not 71, whatever their words.
    if _exact_tokens(a) != _exact_tokens(b):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= QUESTION_CHECKS.restates_key_ratio


def question_problems(question: dict, evidence: list[str]) -> list[str]:
    """Reason codes for one generated question; empty when it passes."""
    problems: list[str] = []
    q_type = question.get("question_type")
    stem = str(question.get("stem") or "")
    bloom = str(question.get("bloom_level") or "").lower()

    if _VAGUE_RE.search(stem) and not _CRITERION_RE.search(stem):
        problems.append(REASON_VAGUE_SUPERLATIVE)

    key: str | None = None
    if q_type == "mcq":
        key_index = mcq_key_index(question)
        if key_index is None:
            return [*problems, REASON_MALFORMED_MCQ]
        options = [str(o) for o in question["options"]]
        key = options[key_index]
        if any(_restates(o, key) for i, o in enumerate(options) if i != key_index):
            problems.append(REASON_DISTRACTOR_RESTATES_KEY)
    elif q_type == "numeric":
        key = str(question.get("correct_answer") or "")

    if key is not None and bloom in _RECALL_LEVELS and not _key_supported(key, evidence):
        problems.append(REASON_UNSUPPORTED_KEY)
    return problems


def higher_order_needed(blooms: list[str], min_share: float) -> int:
    """How many more apply-or-higher questions the quiz needs to meet ``min_share``."""
    if len(blooms) < QUESTION_CHECKS.min_questions_for_share:
        return 0
    have = sum(1 for b in blooms if b in HIGHER_ORDER_LEVELS)
    return max(0, math.ceil(min_share * len(blooms) - 1e-9) - have)
