"""
tests/test_ci_workflow.py
-------------------------
The README and project notes say GitHub Actions runs the tests and lint on
every push. This keeps that true of the workflow file. It does not run CI.
"""

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]


def _workflow() -> dict:
    return yaml.safe_load((_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def _commands(job: dict) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def test_ci_runs_the_backend_suite_on_the_docker_python():
    job = _workflow()["jobs"]["backend"]
    assert "pytest -q" in _commands(job)
    dockerfile = (_REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    base = re.search(r"^FROM python:(\d+\.\d+)", dockerfile, re.MULTILINE)
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python"))
    assert str(setup["with"]["python-version"]) == base.group(1)


def test_ci_runs_the_frontend_tests_build_and_lint():
    commands = _commands(_workflow()["jobs"]["frontend"])
    for command in ("npm test", "npm run build", "npm run lint"):
        assert command in commands
