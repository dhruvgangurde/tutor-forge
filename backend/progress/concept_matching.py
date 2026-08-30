"""
progress/concept_matching.py
-----------------------------
Match a question to one of a course's Concept rows.

Why this exists
---------------
Questions had no link to the concept hierarchy: ``Question`` carried only
assessment_id / type / stem / answer_key / bloom / difficulty / points, and the
assessment agent's ``retrieved_concepts`` is a list of Chroma text chunks, not
Concept rows. Without a link, per-concept mastery cannot be derived from graded
work at all.

This module is the single scorer behind both call sites:

  1. generation time — agents/assessment/nodes.persist_assessment_node tags each
     question as it is written. This is the durable path: it sees the question
     stem AND the teacher's topic, and every question generated from now on gets
     tagged here.
  2. backfill — db/backfill_concept_tags.py, a one-off for questions that
     already existed. It sees only the stem, so it runs at a deliberately
     stricter bar (see the threshold constants below).

Design constraints
------------------
  - Deterministic and cheap. No LLM call: this runs once per generated question
    and once per historical question, and a fuzzy model call would make the
    tagging unauditable and non-reproducible.
  - Fails to NULL, never to a guess. A question that cannot be matched
    confidently keeps concept_id=None and simply contributes nothing to
    per-concept mastery. A wrong tag is worse than a missing one: it would show
    a student mastery on a concept their question never covered.
  - Requires a clear winner. A question that matches two concepts about equally
    is ambiguous, so it is left untagged rather than assigned to whichever one
    happened to score a hair higher.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# ── Tokenisation ──────────────────────────────────────────────────────────────

#: Words with no discriminating power between concepts. Deliberately small and
#: generic — this is not a domain stoplist, and anything domain-specific must
#: stay in so it can carry signal.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "have", "has", "had", "can", "could", "will", "would",
    "should", "what", "why", "how", "when", "where", "which", "who", "this",
    "that", "these", "those", "it", "its", "as", "than", "then", "there",
    "given", "using", "use", "used", "following", "explain", "describe",
    "calculate", "compute", "define", "state", "consider", "context",
    "provided", "above", "below", "question", "answer", "example",
})

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Tokens shorter than this carry too little signal (single letters from
#: formulae, "a", "b", matrix names) and would inflate overlap spuriously.
_MIN_TOKEN_LENGTH = 3


def tokenize(text: str) -> set[str]:
    """Lowercase word set with stopwords and very short tokens removed."""
    return {
        w
        for w in _WORD_RE.findall((text or "").lower())
        if len(w) >= _MIN_TOKEN_LENGTH and w not in _STOPWORDS
    }


def concept_terms(name: str, keywords_json: str | None) -> set[str]:
    """
    The term set identifying one concept: its name plus its stored keywords.

    ``Concept.keywords`` is a JSON array stored as text (see db/models.py). Bad
    or absent JSON degrades to name-only rather than raising — a malformed
    keyword blob must not break assessment generation.
    """
    terms = tokenize(name)
    if keywords_json:
        try:
            parsed = json.loads(keywords_json)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            for kw in parsed:
                if isinstance(kw, str):
                    terms |= tokenize(kw)
    return terms


# ── Scoring ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConceptMatch:
    """A resolved match, carrying enough detail to explain itself in a log."""
    concept_id: object          # uuid.UUID in practice; kept loose so the
                                # scorer stays testable without ORM objects
    score: float
    runner_up_score: float
    matched_terms: frozenset[str]


def score(text_terms: set[str], terms: set[str]) -> float:
    """
    Fraction of a concept's own terms that appear in the text.

    Coverage of the *concept*, not of the text: a question stem is much longer
    than a concept name, so scoring by text coverage would push every score
    toward zero and make the thresholds meaningless.
    """
    if not terms:
        return 0.0
    return len(text_terms & terms) / len(terms)


# ── Thresholds ────────────────────────────────────────────────────────────────
#
# Two bars, and the difference is deliberate.
#
# Generation time sees the question stem *and* the teacher's topic string, so
# there is more genuine signal behind a match and the bar can be the working
# default.
#
# The backfill sees only a stem written before any of this existed, so it is
# held to a visibly stricter bar. The point of tagging at generation time is
# that it is trustworthy; letting an approximate historical match in at the same
# confidence would erode exactly that. When the backfill is unsure it leaves
# NULL, and that question contributes to course-level progress only.

GENERATION_MIN_SCORE = 0.5
GENERATION_MIN_MARGIN = 0.15

BACKFILL_MIN_SCORE = 0.7
BACKFILL_MIN_MARGIN = 0.25


def match_concept(
    text: str,
    candidates: list[tuple[object, str, str | None]],
    *,
    min_score: float,
    min_margin: float,
) -> ConceptMatch | None:
    """
    Best-matching concept for ``text``, or None when the match is not clear.

    ``candidates`` is ``[(concept_id, name, keywords_json), ...]`` — plain tuples
    rather than ORM rows so this can be unit-tested without a database.

    Returns None when either bar fails:
      - the best score is below ``min_score`` (too weak), or
      - it does not beat the runner-up by ``min_margin`` (too ambiguous).
    """
    text_terms = tokenize(text)
    if not text_terms or not candidates:
        return None

    scored: list[tuple[float, object, frozenset[str]]] = []
    for concept_id, name, keywords_json in candidates:
        terms = concept_terms(name, keywords_json)
        s = score(text_terms, terms)
        scored.append((s, concept_id, frozenset(text_terms & terms)))

    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, best_id, matched = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    if best_score < min_score:
        return None
    if best_score - runner_up < min_margin:
        return None

    return ConceptMatch(
        concept_id=best_id,
        score=best_score,
        runner_up_score=runner_up,
        matched_terms=matched,
    )
