"""
grading/numeric.py
------------------
Parsing and comparison for numeric answers and numeric answer keys.

A numeric question's key is usually one number ("20"), but the assessment
generator also produces ordered lists -- "3,27,38,43" for "what is the final
sorted array?". The grader used to call float() on the key, which raises for a
list, so every such question scored 0 for every student, including one who
typed the key exactly, with feedback blaming the student's answer.

Used by the grading node (comparison) and by the teacher question-edit route
(rejecting a key that is neither a number nor a list of numbers).
"""

from __future__ import annotations

import math
import re

from agents.grading.prompts import NUMERIC_TOLERANCE

_SEPARATORS = re.compile(r"[,;]")


def parse_numeric_answer(raw: object) -> list[float] | None:
    """
    A number, or an ordered list of numbers separated by commas or semicolons,
    as floats. None when it is empty or any part is not a finite number.

        "20"            -> [20.0]
        "3, 27, 38, 43" -> [3.0, 27.0, 38.0, 43.0]
        "[3,27,38,43]"  -> [3.0, 27.0, 38.0, 43.0]   (one pair of brackets allowed)

    Commas always separate values: "1,000" is the two-value list [1, 0], not one
    thousand. Before this module such a key failed to parse altogether.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) >= 2 and text[0] in "[(" and text[-1] in "])":
        text = text[1:-1].strip()
    if not text:
        return None
    parts = [p.strip() for p in _SEPARATORS.split(text)]
    if any(not p for p in parts):
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    return values


def within_tolerance(student: float, expected: float) -> bool:
    """±NUMERIC_TOLERANCE relative to the expected value (absolute when it is 0)."""
    tolerance = abs(expected) * NUMERIC_TOLERANCE if expected != 0 else NUMERIC_TOLERANCE
    return abs(student - expected) <= tolerance
