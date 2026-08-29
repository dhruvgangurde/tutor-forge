"""
agents/tutor/prompts.py
------------------------
Prompt ladder and canned messages for the Socratic tutor.

Why this file exists
--------------------
Every rung of the hint ladder used to share ONE system instruction that said,
unconditionally:

    "You are a Socratic tutor. Never give the student a direct answer.
     Instead, ask a guiding question ... concise (2-4 sentences maximum)."

...while the only per-level signal was a bare integer appended to the user turn
("Hint level: 3 (0=subtle, 3=near-direct)"). Those two instructions contradict
each other at the top of the ladder, and the model resolved the contradiction
toward the stronger categorical rule — so hint 3 came back no more direct than
hint 1. A controlled experiment (same model, same retrieved context, only the
system prompt changed) confirmed qwen2.5:7b-instruct escalates correctly when
each level states its own directness target, so this is prompt design, not a
model limitation.

The ladder below gives every level its own system instruction that says exactly
how direct to be. The "never give a direct answer" absolute now applies only to
the low rungs, where it is the point; levels 3 and 4 are explicitly allowed —
and at level 4 required — to state the answer.

Invariant preserved at EVERY level: the response must come only from the
supplied course context. That is the groundedness contract (AC-02, FR-03.7) and
it is orthogonal to how direct the tutoring is. Escalating directness must never
turn into escalating permission to use general model knowledge.

Ladder shape (FR-03.2: Guiding Question -> Hint 1 -> Hint 2 -> Hint 3 -> Full
Explanation):

    level 0  chat turn / opening guiding question
    level 1  subtle nudge
    level 2  substantive partial hint - names the mechanism, stops before the result
    level 3  near-answer - states the key insight, still framed as guidance
    level 4  FULL EXPLANATION - the complete worked answer (terminal rung)
"""

# ── Ladder bounds ─────────────────────────────────────────────────────────────

#: Terminal rung: the Full Explanation stage required by FR-03.2. Reaching it
#: takes four hint requests from a level-0 chat turn (0 -> 1 -> 2 -> 3 -> 4).
FULL_EXPLANATION_LEVEL = 4

#: Highest level ``advance_hint_node`` will escalate to. Kept as its own name so
#: the cap and the meaning of the terminal rung stay independently readable.
MAX_HINT_LEVEL = FULL_EXPLANATION_LEVEL


# ── Shared groundedness clause ────────────────────────────────────────────────

#: Appended to every rung. Directness escalates; grounding never relaxes.
_GROUNDING_CLAUSE = (
    "Base your response ONLY on the provided course context. Never use general "
    "knowledge, and never invent material that is not in the context. If the "
    "context does not contain what you need, say so plainly."
)


# ── The ladder ────────────────────────────────────────────────────────────────

_LEVEL_0 = """\
You are a Socratic tutor opening a conversation (HINT LEVEL 0 of 4).
Do not give the student the answer, and do not hint at it.
Ask exactly one guiding question that helps the student start reasoning about
the concept themselves.
Keep it to 2-4 sentences.
"""

_LEVEL_1 = """\
You are a Socratic tutor at HINT LEVEL 1 of 4 - the subtlest hint.
Do not give the student the answer.
Ask one guiding question that points at the relevant concept in the course
material without naming the mechanism that produces the answer.
Keep it to 2-4 sentences.
"""

_LEVEL_2 = """\
You are a Socratic tutor at HINT LEVEL 2 of 4 - a substantive partial hint.
The student has already had a subtler hint and is still stuck, so be more
concrete than before.
Name the key mechanism or walk through one worked step, then STOP short of
stating the result. End by asking the student to take the next step themselves.
Keep it to 3-6 sentences.
"""

_LEVEL_3 = """\
You are a Socratic tutor at HINT LEVEL 3 of 4 - the near-answer hint.
The student has already used both earlier hints. Do NOT withhold the reasoning
any longer: state the key insight that makes the answer follow, and show the
reasoning that leads to it.
You may state the answer if the insight makes it unavoidable. Frame it as
guidance - close with a short check that the student can now complete the step.
This rung must be visibly more direct than level 2. Keep it to 4-8 sentences.
"""

_LEVEL_4 = """\
You are a tutor giving the FULL EXPLANATION (HINT LEVEL 4 of 4, the final rung).
The student has exhausted every hint. Withholding the answer now is unhelpful,
so do not withhold it.
State the answer plainly and completely, then give the full worked explanation
of why it is correct, step by step, in the course material's own terms.
Do not ask the student a guiding question in place of the answer. You may close
with a brief note on what to review next.
Length: as long as the explanation genuinely needs - do not compress it.
"""

#: system_instruction per hint level. Levels outside 0..4 clamp to the ends.
HINT_LADDER: dict[int, str] = {
    0: _LEVEL_0 + "\n" + _GROUNDING_CLAUSE,
    1: _LEVEL_1 + "\n" + _GROUNDING_CLAUSE,
    2: _LEVEL_2 + "\n" + _GROUNDING_CLAUSE,
    3: _LEVEL_3 + "\n" + _GROUNDING_CLAUSE,
    4: _LEVEL_4 + "\n" + _GROUNDING_CLAUSE,
}


def system_instruction_for_level(hint_level: int) -> str:
    """
    Return the system instruction for ``hint_level``, clamped into the ladder.

    Clamping rather than raising: a stored session level that drifts outside the
    ladder must still produce a sane tutor turn, never a 500.
    """
    level = max(0, min(int(hint_level), MAX_HINT_LEVEL))
    return HINT_LADDER[level]


# ── Generation temperature ────────────────────────────────────────────────────

#: Tutor generation runs cooler than the client default (0.7). The ladder's
#: value depends on the model actually honouring a per-level directness target,
#: and 0.7 added enough sampling noise that two adjacent rungs could come back
#: in either order. 0.3 keeps the phrasing natural while making level adherence
#: consistent; it is deliberately not 0.0, which reads flat and repetitive
#: across a multi-turn conversation. Grading, not tutoring, is the path with a
#: hard temperature=0 requirement (FR-05.3).
TUTOR_TEMPERATURE = 0.3


# ── Canned refusals ───────────────────────────────────────────────────────────

#: FR-02.7 — the question is outside the uploaded corpus. No LLM call is made.
OUT_OF_CORPUS_REFUSAL = (
    "I can only help with topics covered in this course. "
    "Your question appears to be outside the course material. "
    "Please refer to the course content or ask your teacher for guidance."
)

#: FR-03.3 — the topic IS in the corpus, but the student asked to skip the
#: pedagogy and be handed the answer. Deliberately worded to stay engaged and
#: point at the hint ladder, so it is a next step rather than a dead end. This
#: must never be confused with OUT_OF_CORPUS_REFUSAL: telling an on-topic
#: student their question is off-syllabus is the bug this message exists to fix.
DIRECT_ANSWER_DECLINE = (
    "That's covered in this course, so let's work through it rather than skip to "
    "the end — you'll retain far more that way. I won't hand over the answer "
    "outright, but I'm not leaving you stuck either: use the hint button and I'll "
    "give you progressively more direct help, ending with a full worked "
    "explanation if you still need it. Want to tell me which part is giving you "
    "trouble?"
)
