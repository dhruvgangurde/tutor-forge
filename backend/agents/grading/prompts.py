"""
agents/grading/prompts.py
--------------------------
Prompt templates for the Grading Agent.

Design principles (from PROJECT-BRIEF):
  - Temperature = 0 for all grading calls (deterministic output required).
  - All grading must be traceable to retrieved course evidence.
  - The model scores each rubric criterion independently to prevent
    halo effects from holistic scoring.
  - Language must always say "Recommended Score" / "Suggested Feedback",
    never "Final Grade" or "Official Grade".

Prompt engineering choices:
  - JSON-only output: the system instruction prohibits conversational text.
  - Criterion-level scoring: one LLM call per question (all criteria batched),
    not one call per criterion — reduces latency while keeping granularity.
  - Evidence is quoted verbatim in citations so the teacher can verify it.
  - Score must be a float within [0, max_points]; the node clamps it if needed.
"""

# ── System instruction ────────────────────────────────────────────────────────

GRADING_SYSTEM_INSTRUCTION = """\
You are a deterministic educational grading assistant operating at temperature 0.

Rules you MUST follow:
1. Score only against the provided rubric criteria — do not invent criteria.
2. Every score must be justified by quoting verbatim text from the provided
   course evidence. Do not use general knowledge.
3. If the student response does not address a criterion, assign 0 points for
   that criterion and explain why in the feedback.
4. Never award more points than the criterion's max_points value.
5. Output ONLY valid JSON — no preamble, no markdown fences, no commentary.
6. Use the language of recommendation: say "Recommended Score" in feedback,
   never "Final Grade" or "Official Grade".
7. Be consistent: the same answer must always produce the same scores.\
"""

# ── Per-question rubric grading prompt ────────────────────────────────────────

GRADE_SHORT_ANSWER_PROMPT = """\
## Task
Grade the following student response against each rubric criterion listed below.
You must cite retrieved course evidence to justify every score.

---

## Question
{stem}

## Student Response
{student_response}

---

## Rubric Criteria
{rubric_json}

Each criterion object has these fields:
  - criterion_id:  UUID string (preserve exactly in your output)
  - description:   what the student must demonstrate
  - max_points:    maximum points available for this criterion

---

## Retrieved Course Evidence
The following passages were retrieved from the course corpus.
You MUST ground your scoring in this evidence. Do not use knowledge
outside these passages.

{evidence_context}

---

## Required Output Format
Return a single JSON object with this exact structure:

{{
  "criterion_scores": [
    {{
      "criterion_id": "<UUID string from rubric>",
      "description": "<copy of criterion description>",
      "score": <float between 0 and max_points>,
      "max_points": <float>,
      "feedback": "<one or two sentences explaining the score>",
      "citations": [
        {{
          "quoted_text": "<exact verbatim quote from Retrieved Course Evidence above>",
          "source_file": "<filename from evidence>",
          "page_or_slide": <integer or null>
        }}
      ]
    }}
  ],
  "overall_feedback": "<two to four sentences of holistic feedback for the student>"
}}

IMPORTANT:
- criterion_id values must match exactly what was provided in the Rubric Criteria.
- score must be a number (not a string), clamped to [0, max_points].
- citations list may be empty [] only if the student response clearly earns 0 points
  and no course evidence is relevant.
- Do not include any text outside the JSON object.
"""

# ── MCQ/Numeric grading (no LLM — used only for logging the method) ──────────

MCQ_GRADING_METHOD = "deterministic_exact_match"
NUMERIC_GRADING_METHOD = "deterministic_numeric_tolerance"
NUMERIC_TOLERANCE = 0.01  # ±1% of the expected value for float comparisons
