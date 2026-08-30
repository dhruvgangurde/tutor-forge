# Assessment-correctness rubric (LLM-as-judge)

Judges whether ONE generated question is correct — that is, whether the answer
the generator marked as correct is actually the right answer, and whether the
question can be answered from the course material at all.

Deliberately NOT a quality rubric. `distractor_judge_rubric.md` already scores
how good the wrong options are; this asks the narrower, harder question the
other rubric cannot catch: a well-formed question with exactly one defensible
answer, where the generator marked the WRONG one as the key, scores 5/5 on
`single_answer` there and is still wrong.

Return **only** a JSON object:

```json
{"key_correct": true, "answerable_from_context": true, "correct": true, "reason": ""}
```

- `key_correct` — Is the marked answer the correct one? Judge against the course
  context, not outside knowledge. If the marked answer contradicts the context,
  this is false.
- `answerable_from_context` — Could a student answer this from the supplied
  course context alone? False if it requires facts absent from the context.
- `correct` — true only if BOTH of the above are true. This is the verdict the
  ≥95% target is measured on.
- `reason` — one short sentence. Required when `correct` is false.

If the context is insufficient for you to judge the marked answer either way,
set `key_correct` to false and say so in `reason`. Do not guess from general
knowledge: an unverifiable key is not a correct key.

## Input

Question type: {question_type}

Question stem:
{stem}

Options (empty for non-MCQ):
{options}

Answer the generator marked correct (given as full text, not a bare letter -
do not re-derive which option this is, it has already been resolved for you):
{correct_answer}

Course context retrieved for this question:
{context}
