"""
tests/test_tutor_plain_math.py
-------------------------------
Unit tests for agents/tutor/plain_math.py: simple LaTeX in a tutor reply becomes
readable plain text, and anything that is not LaTeX is left alone.
"""

import pytest

from agents.tutor.plain_math import to_plain_math


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Seen in the Ollama replay: the model's own parentheses as delimiters.
        (r"Think about ( V = \frac{W}{Q} ). What is W?", "Think about V = W/Q. What is W?"),
        (r"So \( V = \frac{W}{Q} \) here.", "So V = W/Q here."),
        (r"\[ E = mc^2 \]", "E = mc²"),
        (r"Use $I = \frac{V}{R}$ to check.", "Use I = V/R to check."),
        (r"$$P = IV$$", "P = IV"),
        (r"\frac{a + b}{2}", "(a + b)/2"),
        (r"\dfrac{1}{\frac{x}{y}}", "1/(x/y)"),
        (r"F = m \times a", "F = m × a"),
        (r"W = Q \cdot V", "W = Q · V"),
        (r"V_{AB} = V_A - V_B", "V_AB = V_A - V_B"),
        (r"R_{1} + R_2", "R₁ + R₂"),
        (r"x^{2} + y^3 + 10^{-19}", "x² + y³ + 10⁻¹⁹"),
        (r"e^{i\pi}", "e^(iπ)"),
        (r"\sqrt{2} and \sqrt{x + 1}", "√2 and √(x + 1)"),
        (r"\Delta V \approx 5\,\text{V}", "ΔV ≈ 5 V"),
        (r"\left( \frac{W}{Q} \right)", "( W/Q )"),
        (r"\mathbf{F} \neq 0 \leq 1", "F ≠ 0 ≤ 1"),
        (r"O(n \log n)", "O(n log n)"),
    ],
)
def test_simple_latex_becomes_plain_text(raw, expected):
    assert to_plain_math(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "What do you think happens when you push two charges together?",
        "It costs $5 and then $10 more.",
        "In snake_case names the underscore stays.",
        "A set like {1, 2, 3} is not LaTeX.",
        "Look at O(n^2) for a moment.",
    ],
)
def test_text_without_latex_is_unchanged(text):
    assert to_plain_math(text) == text


def test_code_is_left_exactly_as_written():
    text = (
        "Try this \\(x^2\\) idea:\n\n"
        "```c\nprintf(\"%d\\n\", x);\nint a[] = {1, 2};\n```\n\n"
        "and the call `puts(\"a\\tb\")`."
    )
    out = to_plain_math(text)
    assert out.startswith("Try this x² idea:")
    assert '```c\nprintf("%d\\n", x);\nint a[] = {1, 2};\n```' in out
    assert '`puts("a\\tb")`' in out


def test_empty_text_is_unchanged():
    assert to_plain_math("") == ""


def test_every_instruction_asks_for_plain_text_maths():
    from agents.tutor.prompts import HINT_LADDER, reply_instruction

    for text in [*HINT_LADDER.values(), reply_instruction(False), reply_instruction(True, 3)]:
        assert "Write any maths as plain text" in text
        assert "Never use LaTeX" in text
