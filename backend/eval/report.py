"""
eval/report.py
---------------
Render suite results to a committed, reviewable artifact.

Two files per run in eval/results/: a Markdown report a human reads and a JSON
file a later run can diff against. The point of committing them is that "zero
leakage" stops being a claim in a brief and becomes a number with a date, a
provider, and a probe set attached to it.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

from eval.harness import RESULTS, SuiteResult, utc_stamp

# The brief's full acceptance table (docs/PROJECT-BRIEF.md §6). Criteria with no
# suite are listed anyway, with why — an unmeasured criterion silently missing
# from the report is how "unmeasured" persists.
BRIEF_CRITERIA = [
    ("Teacher acceptance rate", ">=80%", "acceptance-proxy"),
    ("Out-of-corpus leakage", "0", "groundedness"),
    ("Grading variance", "0", "grading-variance"),
    ("Paraphrase score delta", "<=5%", "paraphrase-bias"),
    ("Assessment correctness", ">=95%", "assessment-correctness"),
    ("LLM-as-Judge score", ">=4/5", "distractor-quality"),
    ("Observability coverage", "100%", "observability"),
    ("Hint ladder demo", "<3 minutes", None),
]

_UNCOVERED = {
    "Hint ladder demo": (
        "Manual by definition. Checklist in "
        "eval/datasets/hint_ladder_demo_checklist.md."
    ),
}

_STATUS_MARK = {
    "pass": "PASS",
    "fail": "FAIL",
    "not_measurable": "NOT MEASURABLE",
    "skipped": "SKIPPED",
}


def _env_block() -> dict:
    from core.config import settings

    return {
        "generated_at": utc_stamp(),
        "llm_provider": settings.llm_provider,
        "groundedness_threshold": settings.active_groundedness_threshold,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def render_markdown(results: list[SuiteResult], env: dict) -> str:
    by_name = {r.name: r for r in results}
    lines: list[str] = []
    a = lines.append

    a("# TutorForge evaluation report")
    a("")
    a(f"- **Generated:** {env['generated_at']}")
    a(f"- **Provider:** `{env['llm_provider']}`")
    a(f"- **Groundedness threshold:** {env['groundedness_threshold']}")
    a(f"- **Python:** {env['python']}")
    a("")
    if env["llm_provider"] != "gemini":
        a(
            f"> **Provider caveat.** This run used `{env['llm_provider']}`. "
            "PROJECT-CONTEXT.md states that acceptance-criteria numbers are only "
            "reportable under `LLM_PROVIDER=gemini`; treat everything below as "
            "development signal until re-run on Gemini."
        )
        a("")

    a("## Acceptance criteria (docs/PROJECT-BRIEF.md §6)")
    a("")
    a("| Criterion | Target | Measured | Status |")
    a("| --- | --- | --- | --- |")
    for criterion, target, suite_name in BRIEF_CRITERIA:
        result = by_name.get(suite_name) if suite_name else None
        if result is None:
            measured = _UNCOVERED.get(criterion, "Not measured.")
            a(f"| {criterion} | {target} | {measured} | MANUAL / UNCOVERED |")
        else:
            a(
                f"| {criterion} | {target} | {result.headline} | "
                f"{_STATUS_MARK.get(result.status, result.status.upper())} |"
            )
    a("")

    for result in results:
        a(f"## {result.name} — {_STATUS_MARK.get(result.status, result.status)}")
        a("")
        a(f"**Criterion:** {result.criterion} (target {result.target})")
        a("")
        a(result.headline)
        a("")
        if result.metrics:
            a("### Metrics")
            a("")
            a("```json")
            a(json.dumps(result.metrics, indent=2, default=str))
            a("```")
            a("")
        if result.notes:
            a("### Notes")
            a("")
            for note in result.notes:
                a(f"- {note}")
            a("")
        if result.rows:
            a(f"<details><summary>Per-case results ({len(result.rows)})</summary>")
            a("")
            a("```json")
            a(json.dumps(result.rows, indent=2, default=str))
            a("```")
            a("")
            a("</details>")
            a("")

    a("## Reproducing")
    a("")
    a("```bash")
    a("cd backend")
    a("python -m eval.runner --all")
    a("```")
    a("")
    return "\n".join(lines)


def write(results: list[SuiteResult]) -> tuple[Path, Path]:
    """Write the Markdown + JSON artifacts and return their paths."""
    env = _env_block()
    RESULTS.mkdir(parents=True, exist_ok=True)
    date = env["generated_at"][:10]

    md_path = RESULTS / f"eval-report-{date}.md"
    json_path = RESULTS / f"eval-report-{date}.json"

    md_path.write_text(render_markdown(results, env), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {"environment": env, "suites": [r.to_dict() for r in results]},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return md_path, json_path
