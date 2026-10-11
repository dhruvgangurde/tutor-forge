# TutorForge evaluation suite

Measures the acceptance criteria in `docs/PROJECT-BRIEF.md` §6 against the real
system. Every number in a report came from executing the actual agents, with
stated exceptions: the offline `tutor-pedagogy` and `grading-checks` suites
replay recorded model outputs (see below).

## Why pytest and not promptfoo

`docs/PROJECT-BRIEF.md` §8 names "pytest + promptfoo" as the eval stack, and
`backend/eval/` was reserved for it. This suite is Python/pytest-shaped instead,
deliberately:

- Every probe needs the *application's own* wiring — `RetrievalService` with the
  project's Chroma collections, `settings.active_groundedness_threshold`, the
  compiled LangGraph graphs. promptfoo drives an HTTP/CLI provider, so hitting
  those would mean standing up a shim endpoint per suite and running a second
  toolchain (Node) in the backend's CI job.
- Three of the four suites measure *system* behaviour (a gate decision, score
  reproducibility across runs, per-criterion deltas), not prompt output quality.
  Only the distractor rubric is prompt-quality work, which is promptfoo's actual
  strength — and it is a single LLM-as-judge call that is cheaper to run inline.

The LLM-as-judge rubric is kept as a standalone, portable prompt
(`datasets/distractor_judge_rubric.md`) so it can be lifted into a promptfoo
config later without rewriting it.

## Layout

```
eval/
  datasets/      labeled inputs, committed and reviewable
  suites/        one module per acceptance criterion
  runner.py      CLI: executes suites, writes a report
  report.py      renders results to Markdown + JSON
  results/       generated reports, not committed (gitignored)
```

## Running

Requires a configured backend environment (`backend/.env`) and, for the suites
that generate or grade text, a reachable LLM provider.

```bash
cd backend
python -m eval.runner --suite groundedness        # no LLM needed; retrieval only
python -m eval.runner --suite grading-variance
python -m eval.runner --suite paraphrase-bias
python -m eval.runner --suite distractor-quality
python -m eval.runner --suite tutor-pedagogy       # offline: no LLM, no .env needed
python -m eval.runner --suite grading-checks       # offline; --live grades with the real model
python -m eval.runner --all
```

Results are written to `eval/results/eval-report-<UTC date>.md` and `.json`.
That folder is gitignored (`backend/eval/results/` in the root `.gitignore`), so
reports are not committed; measured numbers go in a short dated summary in the
docs instead.

## tutor-pedagogy (offline)

Findings T1-T6 and G1 from live testing on Gemini, as recorded cases
(`datasets/tutor_pedagogy.json`): the live tutor replies and quiz questions,
a synthetic sorting lab guide as the course, golden facts per question, and
scripted conversations. It replays the recorded model outputs through the real
tutor graph, tutoring service (in-memory SQLite), output guard and question
validator, with real retrieval over an in-memory Chroma collection using the
mock provider's embeddings and bar. So it measures the deterministic layers,
not the model, and its refusal numbers are on the mock embedder's scale.

It reports leakage at hint levels 0-2, on-topic refusals, repeated questions,
fallbacks on non-leaking replies, the definition sentence kept or removed, and
MC key support. Live (Gemini) numbers are reported as not measured. It runs in
pytest (`tests/test_eval_tutor_pedagogy.py`) as a regression gate: fixed live
failures must stay fixed, and any leak, repeat or mismatch it finds must be a
documented `known_gap` in the dataset.

## grading-checks (offline; live with --live)

Cases in `datasets/grading_checks.json`: one short answer with a two-criterion
rubric answered fully, partly, with fractional credit, blank, as whitespace, as
gibberish, and with an instruction in the answer (alone and on top of
gibberish); plus MCQ and numeric answers including blank and injected ones.
Every case is graded twice through the real `grade_responses_node`.

Offline it checks what the code enforces whatever the model says: identical
prompts and scores across the two runs, through `generate_deterministic()`; a
blank answer scores 0 without a model call; MCQ and numeric scores match the
expected ones; the result's criteria are exactly the rubric's; every score stays
within the rubric maximum; and the answer reaches the prompt only inside its
untrusted block. A recorded "obedient" output shows the bound for an injected
answer: the rubric maximum, no more. Whether a real model obeys, or scores
gibberish low, is the `--live` run, which also checks the score ordering
(gibberish <= partial <= full; an injected answer <= the same answer without
it). Live is never run by pytest. The offline run is a pytest gate
(`tests/test_eval_grading_checks.py`).

## What is NOT measured here, and why

- **Grading out-of-corpus leakage.** The grading agent has no groundedness gate
  at all (`docs/BUG-AUDIT-2026-08-15.md` Critical #2 — confirmed still open: no
  `is_grounded` call exists anywhere in `agents/grading/`). There is no gate
  decision to probe, so the leakage suite covers the tutor and assessment agents
  only and says so in the report rather than reporting a vacuous pass.
- **Teacher acceptance rate.** The brief's ≥80% target is measured by a human
  teacher accepting or overriding AI recommendations. The suite here measures a
  *proxy* — agreement between the AI's recommended score and a labeled expected
  score — and labels it as a proxy in the report. It is not a teacher-acceptance
  number and must not be quoted as one.
- **Hint-ladder demo under 3 minutes.** A live demonstration, by definition. The
  checklist lives in `datasets/hint_ladder_demo_checklist.md`.
