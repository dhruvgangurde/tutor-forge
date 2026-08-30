"""
eval/suites/observability.py
-----------------------------
AC: observability coverage = 100% (docs/PROJECT-BRIEF.md §6, "Langfuse trace
audit").

A static audit of the four agent graphs. It asserts two things about each:

  1. the agent emits a span through the v4 API (``start_observation``), and
  2. it does not call the removed v2 ``langfuse.trace()``.

Static rather than runtime on purpose. The v4 break was invisible precisely
because the call was wrapped in a bare ``except`` — a runtime probe against a
Langfuse instance would have gone green while every trace silently failed. What
matters for coverage is that each agent has a live call site at all, and that is
a property of the source.
"""

from __future__ import annotations

from pathlib import Path

from eval.harness import SuiteResult

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
AGENTS = ["ingestion", "tutor", "assessment", "grading"]

V4_CALL = "start_observation("
V2_CALL = "langfuse.trace("


async def run() -> SuiteResult:
    rows: list[dict] = []
    for agent in AGENTS:
        nodes = AGENTS_DIR / agent / "nodes.py"
        source = nodes.read_text(encoding="utf-8") if nodes.exists() else ""
        # Count only real call sites, not the prose in module docstrings.
        v4 = sum(
            1
            for line in source.splitlines()
            if V4_CALL in line and not line.strip().startswith("#")
        )
        v2 = sum(
            1
            for line in source.splitlines()
            if V2_CALL in line and not line.strip().startswith("#")
        )
        rows.append(
            {
                "agent": agent,
                "file": str(nodes.relative_to(AGENTS_DIR.parent)),
                "v4_span_calls": v4,
                "v2_dead_calls": v2,
                "traced": v4 > 0 and v2 == 0,
            }
        )

    traced = [r for r in rows if r["traced"]]
    dead = [r for r in rows if r["v2_dead_calls"] > 0]
    untraced = [r for r in rows if r["v4_span_calls"] == 0]
    coverage = len(traced) / len(rows) if rows else 0.0

    notes = [
        "Static audit of agent source, not a runtime probe: the v4 break was "
        "hidden by a bare except, so a runtime check would have reported green "
        "while every trace failed.",
    ]
    if dead:
        notes.append(
            "Agents still calling the removed v2 API: "
            + ", ".join(r["agent"] for r in dead)
        )
    if untraced:
        notes.append(
            "Agents emitting no span at all: " + ", ".join(r["agent"] for r in untraced)
        )

    return SuiteResult(
        name="observability",
        criterion="Observability coverage",
        target="100%",
        status="pass" if coverage == 1.0 else "fail",
        headline=f"{len(traced)}/{len(rows)} agents emit a live v4 trace span ({coverage:.0%}).",
        metrics={
            "agents": len(rows),
            "traced": len(traced),
            "coverage": round(coverage, 4),
            "agents_with_dead_v2_calls": [r["agent"] for r in dead],
            "agents_with_no_span": [r["agent"] for r in untraced],
        },
        rows=rows,
        notes=notes,
    )
