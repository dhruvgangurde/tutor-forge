"""
tests/test_citation_confidence.py
---------------------------------
Grading evidence chips showed "0%" for every citation (frontend audit #9).

Cause: the confidence of a model citation was looked up by an exact substring
test of its quote against the retrieved chunks. PDF chunks keep the page's
line breaks while the model quotes them with spaces, so the test failed and
the value defaulted to 0.0. These pin the fixed lookup and the API's handling
of rows stored before the fix.
"""

from agents.grading.nodes import citation_confidence
from grading.router import _to_citation

# Shaped like the real chunk behind the audit's chip (DAA lab guide, p. 4).
EVIDENCE = [
    {
        "text": "Merge two sorted piles by always picking the smaller top card – like a dealer\n"
        "combining two sorted hands.\nSTEP 1 – Understand the merge() Function",
        "source_file": "DAA_Complete_Lab_Guide.pdf",
        "page_or_slide": 4,
        "confidence": 0.74,
    },
    {
        "text": "Quick sort picks a pivot and partitions the array around it.",
        "source_file": "DAA_Complete_Lab_Guide.pdf",
        "page_or_slide": 6,
        "confidence": 0.58,
    },
]


def _cite(quote: str, page=4, source="DAA_Complete_Lab_Guide.pdf") -> dict:
    return {"quoted_text": quote, "source_file": source, "page_or_slide": page}


def test_quote_spanning_a_line_break_gets_the_chunk_score():
    quote = "like a dealer combining two sorted hands."
    assert citation_confidence(_cite(quote), EVIDENCE) == 0.74


def test_quote_matching_ignores_case_and_extra_spaces():
    quote = "  QUICK SORT picks a   pivot "
    assert citation_confidence(_cite(quote, page=6), EVIDENCE) == 0.58


def test_paraphrased_quote_falls_back_to_the_cited_page():
    quote = "Merging always takes the smaller of the two top cards."
    assert citation_confidence(_cite(quote, page=6), EVIDENCE) == 0.58


def test_page_given_as_a_string_still_matches():
    assert citation_confidence(_cite("paraphrase", page="4"), EVIDENCE) == 0.74


def test_no_matching_evidence_is_unknown_not_zero():
    assert citation_confidence(_cite("unrelated", page=99), EVIDENCE) is None
    assert citation_confidence(_cite("unrelated", source="other.pdf"), EVIDENCE) is None
    assert citation_confidence(_cite("anything"), []) is None


def test_api_reports_a_real_score():
    out = _to_citation({"text": "t", "source_file": "f.pdf", "page_or_slide": 4, "confidence": 0.74})
    assert out.confidence == 0.74


def test_api_hides_the_old_zero_default_and_missing_scores():
    # Rows graded before the fix stored 0.0 for every unmatched quote.
    assert _to_citation({"text": "t", "source_file": "f.pdf", "confidence": 0.0}).confidence is None
    assert _to_citation({"text": "t", "source_file": "f.pdf", "confidence": None}).confidence is None
    assert _to_citation({"text": "t", "source_file": "f.pdf"}).confidence is None
