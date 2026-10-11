"""
agents/assessment/prompts.py
-----------------------------
Prompt templates for the Assessment Generation Agent.

Design goals:
  - Grounding-first: every prompt instructs the model to use ONLY the
    provided course context and output an empty list if context is insufficient.
  - Strict JSON: no markdown fences, no prose — only the JSON structure specified.
  - Minimal hallucination surface: allowed output values are enumerated explicitly.
  - Prompt injection prevention: user-provided values (topic, difficulty) appear
    inside quoted fields, not as free-form instruction text.
  - Token efficiency: instructions are concise and non-redundant.

Bloom's Taxonomy levels (all lowercase):
    remember | understand | apply | analyze | evaluate | create
"""

# ── System instruction ────────────────────────────────────────────────────────

ASSESSMENT_SYSTEM_INSTRUCTION = (
    "You are an educational assessment designer. "
    "Your ONLY source of knowledge is the COURSE CONTEXT provided in each prompt. "
    "Do NOT use general knowledge, prior training data, or invented facts. "
    "If the context does not contain enough information to write a question, "
    "return an empty questions array. "
    "Output ONLY valid JSON — no markdown, no commentary, no explanations outside the JSON."
)

# ── Question generation ───────────────────────────────────────────────────────

GENERATE_QUESTIONS_PROMPT = """\
Generate assessment questions using ONLY the course content below.

===COURSE CONTEXT===
{context}
===END CONTEXT===

REQUIREMENTS:
- Topic: "{topic}"
- Difficulty: "{difficulty}"  (allowed: easy, medium, hard, mixed)
- Total questions: {count}
- Type distribution:
{type_distribution}
- Bloom level distribution:
{bloom_distribution}

RULES:
1. Every question stem must be directly traceable to a fact or concept in the context above.
2. Do NOT add information not present in the context.
3. If context is insufficient for any question slot, return fewer questions rather than hallucinating.
4. mcq: exactly 4 options; exactly one correct; include only the letter (A/B/C/D) in correct_answer.
   Option TEXT must be plain, with NO "A."/"B."/"(A)" prefix - position in the array is the letter.
   The interface renders the letter itself; a prefix here is displayed twice ("A. A. Two billion years ago").
5. short_answer: open-ended, requires 2-5 sentences; include 2-3 rubric_criteria with max_points per criterion.
6. numeric: exact calculation or estimation with a numeric correct_answer; no rubric_criteria needed.
7. bloom_level must be one of: remember, understand, apply, analyze, evaluate, create.
8. difficulty must be one of: easy, medium, hard.
9. worked_solution must be concise (1-3 sentences or steps).
10. rubric_criteria must be an empty array for mcq and numeric question types.
11. NEVER refer to "the context", "the provided context", "the course context",
    "the information provided", or "the passage" in a stem, an option, or a
    worked_solution. The student sits an exam paper and has never seen your
    context block, so "When did humans emerge in Africa according to the
    context?" is meaningless to them. Write "When did humans emerge in Africa?"
    Ask about the SUBJECT, never about the source document.
12. Distractors must be the SAME KIND OF THING as the correct answer (a date
    question gets date options) and must never be the answer to a DIFFERENT
    question in this set.
13. Exactly one defensible answer. No wrong option may also satisfy the stem:
    never offer a special case of the correct condition or value (if the key is
    "at least 18", then "exactly 18" is also true whenever it applies).
14. Never write "simplest", "best", "easiest", "most appropriate" or a similar
    word in a stem unless the stem states the measure (for example "in terms of
    the number of comparisons") and the context says which option meets it.
15. Each wrong option must be wrong for a reason the context makes clear, and
    must never restate the correct option in other words.
{higher_order_rule}
Return ONLY this JSON, nothing else:
{{
  "questions": [
    {{
      "question_type": "mcq",
      "stem": "...",
      "bloom_level": "remember",
      "difficulty": "easy",
      "options": ["...", "...", "...", "..."],
      "correct_answer": "A",
      "worked_solution": "...",
      "rubric_criteria": []
    }},
    {{
      "question_type": "short_answer",
      "stem": "...",
      "bloom_level": "analyze",
      "difficulty": "medium",
      "options": null,
      "correct_answer": "Key points the answer should cover: ...",
      "worked_solution": "...",
      "rubric_criteria": [
        {{"description": "...", "max_points": 2.0}},
        {{"description": "...", "max_points": 1.0}}
      ]
    }},
    {{
      "question_type": "numeric",
      "stem": "...",
      "bloom_level": "apply",
      "difficulty": "medium",
      "options": null,
      "correct_answer": "42",
      "worked_solution": "...",
      "rubric_criteria": []
    }}
  ]
}}
"""

#: Rule 16 of GENERATE_QUESTIONS_PROMPT, filled in only when the teacher gave
#: no Bloom mix of their own and the quiz has 5 or more questions.
HIGHER_ORDER_RULE = """\
16. At least {percent}% of the questions must be at the apply or analyze level:
    ask the student to trace, predict, compare or work something out from the
    context, not to recall a sentence from it.
"""

# ── Regenerating one question ─────────────────────────────────────────────────

#: Sent once for a question that failed agents/assessment/validation.py. The
#: reasons are fixed text from that module, never the rejected question.
REGENERATE_QUESTION_PROMPT = """\
Write ONE assessment question using ONLY the course content below.

===COURSE CONTEXT===
{context}
===END CONTEXT===

REQUIREMENTS:
- Topic: "{topic}"
- Question type: "{question_type}"
- Bloom level: "{bloom_level}"
- Difficulty: "{difficulty}"

An earlier question for this slot was rejected because:
{reasons}

It must ask something different from these questions already in the quiz:
{other_questions}

RULES:
1. The stem and the correct answer must be directly traceable to the context.
2. mcq: exactly 4 plain-text options (no "A." prefix); correct_answer is the letter A-D.
   Exactly one option is defensible; no wrong option restates or is a special
   case of the correct one.
3. short_answer: include 2-3 rubric_criteria with max_points; numeric: a numeric
   correct_answer and no rubric_criteria; mcq: an empty rubric_criteria array.
4. Never write "simplest", "best" or a similar word without stating the measure.
5. NEVER refer to "the context", "the passage" or the course material in the
   stem, an option or the worked_solution.

Return ONLY this JSON, nothing else:
{{
  "question": {{
    "question_type": "{question_type}",
    "stem": "...",
    "bloom_level": "{bloom_level}",
    "difficulty": "{difficulty}",
    "options": ["...", "...", "...", "..."],
    "correct_answer": "...",
    "worked_solution": "...",
    "rubric_criteria": []
  }}
}}
"""

# ── Distractor improvement ────────────────────────────────────────────────────

IMPROVE_DISTRACTORS_PROMPT = """\
Improve the three incorrect MCQ options for the question below.
Use ONLY the course context provided — do not invent facts.

===COURSE CONTEXT===
{context}
===END CONTEXT===

QUESTION: "{stem}"
CORRECT ANSWER: Option {correct_answer}
CURRENT OPTIONS:
{options}

RULES FOR THE THREE INCORRECT OPTIONS:
1. SAME KIND OF THING as the correct answer. If the correct answer is a date,
   every option is a date. If it is a mass, every option is a mass in the same
   units. A question asking WHEN something happened must not offer an area or a
   weight as an option - that is not a wrong answer, it is not an answer at all.
2. Answers to THIS question only. Never reuse a figure or statement that answers
   a DIFFERENT question, even if it appears in the context. Pulling the most
   salient number out of the context is the specific failure this rule exists to
   stop.
3. Plausible — a student with partial understanding could choose them.
4. Grounded — consistent with the context, not invented.
5. Clearly wrong — unambiguously incorrect to a well-prepared student.
6. Grammatically parallel to the correct option in length and structure.
7. Do NOT change the text or position of the correct answer option.
8. Return all 4 options in order, as PLAIN TEXT with no "A."/"B." prefix.

Return ONLY this JSON:
{{
  "improved_options": ["...", "...", "...", "..."]
}}
"""
