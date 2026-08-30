"""
eval/harness.py
----------------
Shared plumbing for the eval suites: dataset loading, result records, and the
service wiring the suites need.

The suites drive the application's own code — RetrievalService with the real
Chroma collections, the real groundedness threshold, the real grading node — so
a number produced here reflects the system a user would hit. Nothing in this
package asserts against a mock.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATASETS = Path(__file__).parent / "datasets"
RESULTS = Path(__file__).parent / "results"


def load_dataset(name: str) -> dict:
    """Load a committed dataset by filename."""
    with open(DATASETS / name, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class SuiteResult:
    """
    One suite's outcome.

    ``status`` is one of:
      pass       — the criterion's target was met
      fail       — the target was measured and missed
      not_measurable — the system lacks the mechanism the criterion describes,
                   so no number is reported (a vacuous pass would be worse)
      skipped    — preconditions absent (no corpus, no provider)
    """
    name: str
    criterion: str
    target: str
    status: str
    headline: str
    metrics: dict[str, Any] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_services():
    """
    Construct RetrievalService and the LLM clients exactly as main.lifespan does.

    Imported lazily and built here rather than booting the FastAPI app: the
    suites need the singletons, not the HTTP layer.
    """
    import chromadb

    from core.config import settings
    from main import _build_llm_clients
    from retrieval.service import RetrievalService

    gemini_pro, gemini_flash = _build_llm_clients()
    chroma = chromadb.PersistentClient(path=settings.chroma_persist_path)
    retrieval = RetrievalService(chroma_client=chroma, gemini_client=gemini_pro)
    return retrieval, gemini_pro, gemini_flash


async def courses_by_name() -> dict[str, uuid.UUID]:
    """Map ready course names to ids, so probes can target a real corpus."""
    from sqlalchemy import select

    from core.database import AsyncSessionFactory
    from db.models import Course

    async with AsyncSessionFactory() as db:
        rows = (
            await db.execute(select(Course.name, Course.id).where(Course.status == "ready"))
        ).all()
    return {name: cid for name, cid in rows}


def grading_response(
    *,
    question_type: str,
    stem: str,
    answer_key: dict,
    max_points: float,
    answer_text: str | None = None,
    answer_choice: str | None = None,
    rubric_criteria: list[dict] | None = None,
) -> dict:
    """
    Build one response dict in the exact shape load_submission_node produces.

    Lets the grading suites exercise grade_responses_node — the real scoring
    code — without staging a Submission row per case. The shape is asserted by
    tests/test_grading_unit.py, so a drift here would break those first.
    """
    return {
        "question_id": str(uuid.uuid4()),
        "question_type": question_type,
        "stem": stem,
        "answer_key": answer_key,
        "answer_text": answer_text,
        "answer_choice": answer_choice,
        "max_points": max_points,
        "rubric_criteria": [
            {
                "criterion_id": str(uuid.uuid4()),
                "description": c["description"],
                "max_points": c["max_points"],
                "order_index": i,
            }
            for i, c in enumerate(rubric_criteria or [])
        ],
    }


def grading_state(
    responses: list[dict],
    course_id: uuid.UUID | None = None,
    evidence: list[list[dict]] | None = None,
) -> dict:
    """
    A minimal GradingState carrying the given responses.

    ``evidence`` must be supplied for short-answer grading — see
    retrieve_evidence() for why an empty list silently forces every score to 0.
    """
    return {
        "submission_id": uuid.uuid4(),
        "course_id": course_id or uuid.uuid4(),
        "responses": responses,
        "evidence_per_question": evidence or [[] for _ in responses],
        "criterion_results": [],
        "total_score": 0.0,
        "max_score": 0.0,
        "recommendation_rationale": {},
        "recommendation_id": None,
        "trace_id": None,
        "status": "running",
        "error": None,
    }


#: Minimum agreement with known verdicts before a judge's output over real data
#: is treated as a measurement. Shared by every LLM-as-judge suite so the bar
#: cannot drift apart between them.
#:
#: This exists because it caught a real failure: the assessment-correctness
#: judge scored 69% here — it flagged 5/5 deliberately mis-keyed questions AND
#: rejected 4/5 known-correct ones, contradicting its own reasoning mid-sentence.
#: Without the gate that suite would have published "8% assessment correctness",
#: which is a false claim about the generator, not a measurement of it.
MIN_JUDGE_ACCURACY = 0.80


@dataclass
class Calibration:
    """How well a judge matched known verdicts, and whether to trust it."""
    items: int
    correct: int
    accuracy: float
    passed: bool
    rows: list[dict] = field(default_factory=list)

    def as_metrics(self) -> dict:
        return {
            "judge_calibration_items": self.items,
            "judge_accuracy": round(self.accuracy, 4),
            "judge_calibration_passed": self.passed,
            "min_judge_accuracy": MIN_JUDGE_ACCURACY,
        }


def calibrate(rows: list[dict], min_accuracy: float = MIN_JUDGE_ACCURACY) -> Calibration:
    """
    Score a judge against labeled items.

    ``rows`` must carry a boolean ``agrees`` (the judge matched the known
    verdict) and may carry ``judged`` = None for an unparseable response, which
    is excluded from the denominator rather than counted as a miss — an
    unparseable answer is a harness problem, not evidence about judgement.
    """
    scored = [r for r in rows if r.get("judged") is not None]
    correct = sum(1 for r in scored if r.get("agrees"))
    accuracy = correct / len(scored) if scored else 0.0
    return Calibration(
        items=len(scored),
        correct=correct,
        accuracy=accuracy,
        passed=bool(scored) and accuracy >= min_accuracy,
        rows=rows,
    )


def retrieve_evidence(
    stem: str,
    course_id,
    retrieval,
    top_k: int = 5,
) -> list[dict]:
    """
    Retrieve course evidence for one question, in the shape grading expects.

    Load-bearing, not a nicety. ``grade_responses_node``'s short-answer prompt
    says, verbatim, that when no evidence was retrieved the model should "award
    0 points and note the absence of evidence". So grading a short answer with
    an empty evidence list does not measure the grader — it measures that
    instruction, and every model answer scores 0.

    The first run of this suite made exactly that mistake: short-answer
    agreement came out at 8/15 while MCQ was 60/60, because the harness passed
    no evidence. Suites that grade free text must retrieve first, the way
    retrieve_evidence_node does in production.
    """
    result = retrieval.retrieve(course_id, stem, top_k=top_k)
    return [
        {
            "text": chunk.text,
            "source_file": chunk.source_file,
            "page_or_slide": chunk.page_or_slide,
            "confidence": float(score),
        }
        for chunk, score in zip(result.chunks, result.confidence_scores)
    ]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
