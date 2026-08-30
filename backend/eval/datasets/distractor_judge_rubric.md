# Distractor-quality rubric (LLM-as-judge)

Scores one generated multiple-choice question. Kept as a standalone prompt so it
can be lifted into a promptfoo config unchanged.

Return **only** a JSON object:

```json
{"plausibility": 0, "distinctness": 0, "single_answer": 0, "grounded": 0, "overall": 0, "notes": ""}
```

Each field is an integer 1-5 except `notes` (a short string). `overall` is your
holistic judgement, not an average.

## Criteria

**plausibility (1-5)** — Would a student who half-understands the material
seriously consider the wrong options? 5 = every distractor reflects a real
misconception. 1 = distractors are obviously absurd, joke options, or "none of
the above" padding.

**distinctness (1-5)** — Are the options meaningfully different from one another?
5 = each option is a distinct claim. 1 = options are restatements of each other,
or two options are both correct by any reasonable reading.

**single_answer (1-5)** — Is exactly one option defensibly correct? 5 =
unambiguous. 1 = multiple defensible answers, or none of them is correct.

**grounded (1-5)** — Is the question answerable from the supplied course context
alone, without outside knowledge? 5 = fully answerable from the context. 1 =
requires facts absent from the context.

## Input

Question stem:
{stem}

Options:
{options}

Marked correct answer:
{correct_answer}

Course context the question was generated from:
{context}
