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
4. mcq: exactly 4 options labelled A, B, C, D; exactly one correct; include only the letter (A/B/C/D) in correct_answer.
5. short_answer: open-ended, requires 2-5 sentences; include 2-3 rubric_criteria with max_points per criterion.
6. numeric: exact calculation or estimation with a numeric correct_answer; no rubric_criteria needed.
7. bloom_level must be one of: remember, understand, apply, analyze, evaluate, create.
8. difficulty must be one of: easy, medium, hard.
9. worked_solution must be concise (1-3 sentences or steps).
10. rubric_criteria must be an empty array for mcq and numeric question types.

Return ONLY this JSON, nothing else:
{{
  "questions": [
    {{
      "question_type": "mcq",
      "stem": "...",
      "bloom_level": "remember",
      "difficulty": "easy",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
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
1. Plausible — a student with partial understanding could choose them.
2. Grounded — based on real content from the context, not invented.
3. Clearly wrong — unambiguously incorrect to a well-prepared student.
4. Grammatically parallel to the correct option in length and structure.
5. Do NOT change the text or position of the correct answer option.
6. Return all 4 options (A, B, C, D) including the unchanged correct one.

Return ONLY this JSON:
{{
  "improved_options": ["A. ...", "B. ...", "C. ...", "D. ..."]
}}
"""
