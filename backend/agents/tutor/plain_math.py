"""
agents/tutor/plain_math.py
---------------------------
Turn simple LaTeX in a tutor reply into plain text a student can read.

The tutor renders markdown, not maths, so a reply such as
"( V = \\frac{W}{Q} )" reached the student exactly like that. The prompt now
asks for plain-text maths; this is the deterministic second line for when the
model writes LaTeX anyway. It covers the simple forms a tutor reply uses
(fractions, roots, powers, subscripts, common symbols and Greek letters, and
math delimiters), not LaTeX in general. A full renderer would be a new
dependency, which this deliberately avoids.

Code spans and fenced code blocks are left exactly as they are: a programming
course can legitimately show "\\n" or braces in code.
"""

from __future__ import annotations

import re

_SYMBOLS = {
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "approx": "≈",
    "neq": "≠", "ne": "≠", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "infty": "∞", "to": "→", "rightarrow": "→", "Rightarrow": "⇒", "propto": "∝",
    "circ": "°", "degree": "°", "ldots": "...", "cdots": "...", "dots": "...",
    "alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ",
    "Delta": "Δ", "epsilon": "ε", "varepsilon": "ε", "theta": "θ", "Theta": "Θ",
    "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "Sigma": "Σ",
    "tau": "τ", "phi": "φ", "Phi": "Φ", "omega": "ω", "Omega": "Ω",
}
_SPACING = {"quad", "qquad"}
#: Greek letters bind to a following single-letter variable: "\Delta V" is "ΔV".
_GREEK = {
    "alpha", "beta", "gamma", "Gamma", "delta", "Delta", "epsilon", "varepsilon",
    "theta", "Theta", "lambda", "mu", "pi", "rho", "sigma", "Sigma", "tau", "phi",
    "Phi", "omega", "Omega",
}
_SUPERSCRIPT = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")

_CODE_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_DELIMITER_RE = re.compile(r"\\[()\[\]]|\$\$")
_DOLLAR_MATH_RE = re.compile(r"\$([^$\n]*[\\^_=][^$\n]*)\$")
#: "( V = \frac{W}{Q} )": the model's own parentheses used as math delimiters.
#: Only when padded inside and holding a command, so ordinary brackets stay.
_PADDED_PARENS_RE = re.compile(r"[(\[]\s+([^()\[\]\n]*?\\[A-Za-z][^()\[\]\n]*?)\s+[)\]]")
_WRAPPER_RE = re.compile(
    r"\\(?:text|textrm|textbf|textit|mathrm|mathbf|mathit|mathsf|operatorname|mbox|"
    r"vec|hat|bar|overline|underline|boldsymbol)\s*\{([^{}]*)\}"
)
_LEFT_RIGHT_RE = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)\b\s*")
_FRAC_RE = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_SUP_RE = re.compile(r"\^\{([^{}]*)\}|\^(-?\w)")
_SUB_RE = re.compile(r"(?<=[A-Za-z0-9)])_\{([^{}]*)\}|(?<=[A-Za-z)])_(\d)")
_COMMAND_RE = re.compile(r"\\([A-Za-z]+)( (?=[A-Za-z]\b))?|\\[,;:! ]")
#: Only text containing one of these is touched at all.
_LATEX_MARKERS = ("\\", "$", "_{", "^{")


def _group(text: str) -> str:
    """Wrap a compound numerator or denominator so a/b still reads correctly."""
    text = text.strip()
    return f"({text})" if re.search(r"[\s+\-*/]", text) else text


def _frac(m: re.Match) -> str:
    return f"{_group(m.group(1))}/{_group(m.group(2))}"


def _sqrt(m: re.Match) -> str:
    inner = m.group(1).strip()
    return f"√{inner}" if len(inner) == 1 else f"√({inner})"


def _sup(m: re.Match) -> str:
    power = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
    if power and all(c in "0123456789+-n" for c in power):
        return power.translate(_SUPERSCRIPT)
    return f"^({power})" if len(power) > 1 else f"^{power}"


def _sub(m: re.Match) -> str:
    index = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
    if index and all(c in "0123456789+-" for c in index):
        return index.translate(_SUBSCRIPT)
    return f"_{index}"


def _command(m: re.Match) -> str:
    name = m.group(1)
    if name is None or name in _SPACING:
        return " "
    space = "" if name in _GREEK else (m.group(2) or "")
    return _SYMBOLS.get(name, name) + space


def _clean_prose(text: str) -> str:
    out = _PADDED_PARENS_RE.sub(r"\1", text)
    out = _DELIMITER_RE.sub("", out)
    out = _DOLLAR_MATH_RE.sub(r"\1", out)
    out = _LEFT_RIGHT_RE.sub("", out)
    for _ in range(5):  # nested wrappers and fractions, innermost first
        new = _FRAC_RE.sub(_frac, _WRAPPER_RE.sub(r"\1", out))
        if new == out:
            break
        out = new
    out = _SQRT_RE.sub(_sqrt, out)
    out = _SUP_RE.sub(_sup, out)
    out = _SUB_RE.sub(_sub, out)
    out = _COMMAND_RE.sub(_command, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" +([.,;:!?])", r"\1", out)


def to_plain_math(text: str) -> str:
    """Return ``text`` with simple LaTeX rewritten as plain text; code untouched."""
    if not text or not any(marker in text for marker in _LATEX_MARKERS):
        return text
    parts = _CODE_RE.split(text)
    # split() with one capturing group puts the code spans at the odd indexes.
    return "".join(p if i % 2 else _clean_prose(p) for i, p in enumerate(parts)).strip()
