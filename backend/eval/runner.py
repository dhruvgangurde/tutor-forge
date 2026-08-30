"""
eval/runner.py
---------------
CLI entry point for the evaluation suites.

    python -m eval.runner --all
    python -m eval.runner --suite groundedness --suite observability
    python -m eval.runner --suite acceptance-proxy --limit 25

Every suite is independent and failure-isolated: one suite raising does not
prevent the others from running or the report from being written, because a
partial report with an explicit error is more useful than no report.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from eval.harness import SuiteResult
from eval.report import write

SUITES = {
    "assessment-correctness": "eval.suites.assessment_correctness",
    "groundedness": "eval.suites.groundedness",
    "grading-variance": "eval.suites.grading_variance",
    "paraphrase-bias": "eval.suites.paraphrase_bias",
    "distractor-quality": "eval.suites.distractor_quality",
    "acceptance-proxy": "eval.suites.acceptance_proxy",
    "observability": "eval.suites.observability",
}

logger = logging.getLogger("eval")


async def _run_suite(name: str, module_path: str, limit: int | None, runs: int) -> SuiteResult:
    import importlib

    module = importlib.import_module(module_path)
    kwargs = {}
    if name == "acceptance-proxy" and limit:
        kwargs["limit"] = limit
    if name == "distractor-quality" and limit:
        kwargs["limit"] = limit
    if name == "assessment-correctness" and limit:
        kwargs["limit"] = limit
    if name == "grading-variance":
        kwargs["runs"] = runs
    return await module.run(**kwargs)


async def main_async(selected: list[str], limit: int | None, runs: int) -> int:
    results: list[SuiteResult] = []
    for name in selected:
        print(f"\n=== running suite: {name} ===", flush=True)
        try:
            result = await _run_suite(name, SUITES[name], limit, runs)
        except Exception as exc:  # noqa: BLE001 - one suite must not sink the run
            logger.exception("Suite %s raised", name)
            result = SuiteResult(
                name=name,
                criterion="(suite raised)",
                target="-",
                status="skipped",
                headline=f"Suite raised: {type(exc).__name__}: {exc}",
                notes=["See the runner log for the traceback."],
            )
        results.append(result)
        print(f"  {result.status.upper()}: {result.headline}", flush=True)

    md_path, json_path = write(results)
    print(f"\nReport written:\n  {md_path}\n  {json_path}")

    failed = [r for r in results if r.status == "fail"]
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TutorForge evaluation suite")
    parser.add_argument(
        "--suite", action="append", choices=sorted(SUITES), help="Suite to run (repeatable)."
    )
    parser.add_argument("--all", action="store_true", help="Run every suite.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap cases for the sized suites."
    )
    parser.add_argument(
        "--runs", type=int, default=10, help="Repeat count for grading-variance (default 10)."
    )
    args = parser.parse_args()

    if not args.all and not args.suite:
        parser.error("pass --all or at least one --suite")

    selected = sorted(SUITES) if args.all else args.suite
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(main_async(selected, args.limit, args.runs))


if __name__ == "__main__":
    sys.exit(main())
