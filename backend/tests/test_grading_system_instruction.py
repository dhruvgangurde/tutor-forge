"""
tests/test_grading_system_instruction.py
----------------------------------------
GRADING_SYSTEM_INSTRUCTION reaches the model. It used to be defined and never
sent: generate_deterministic() took no system instruction on any provider.
Every provider now passes an optional one through, still at temperature 0, and
the grading node sends the grading rules. No model, no network.
"""

import json
from unittest.mock import MagicMock

import pytest

from agents.grading.nodes import grade_responses_node
from agents.grading.prompts import GRADING_SYSTEM_INSTRUCTION
from core.mock_gemini import MockGeminiFlashClient, MockGeminiProClient
from core.ollama_client import OllamaClient
from eval.harness import grading_response, grading_state
from main import GeminiProClient


def _provider(cls):
    client = cls.__new__(cls)  # no network, no key: only generate() is exercised
    client.generate = MagicMock(return_value="{}")
    return client


@pytest.mark.parametrize("cls", [GeminiProClient, OllamaClient, MockGeminiProClient, MockGeminiFlashClient])
def test_every_provider_passes_the_system_instruction_at_temperature_0(cls):
    client = _provider(cls)
    client.generate_deterministic("the prompt", system_instruction="the rules")
    client.generate.assert_called_once_with("the prompt", temperature=0.0, system_instruction="the rules")


@pytest.mark.parametrize("cls", [GeminiProClient, OllamaClient, MockGeminiProClient, MockGeminiFlashClient])
def test_without_one_the_call_is_as_before(cls):
    client = _provider(cls)
    client.generate_deterministic("the prompt")
    client.generate.assert_called_once_with("the prompt", temperature=0.0, system_instruction="")


def _short_answer_state():
    response = grading_response(
        question_type="short_answer", stem="Where does the index i start?", answer_key={"correct_answer": ""},
        max_points=2.0, answer_text="At low - 1.",
        rubric_criteria=[{"description": "Says i starts at low - 1.", "max_points": 2.0}],
    )
    evidence = [[{"text": "The index i starts at low - 1.", "source_file": "lab.md", "page_or_slide": 1, "score": 0.9}]]
    return response, grading_state([response], evidence=evidence)


def _model_for(response):
    cid = response["rubric_criteria"][0]["criterion_id"]
    model = MagicMock()
    model.generate_deterministic.return_value = json.dumps({
        "criterion_scores": [{"criterion_id": cid, "description": "x", "score": 2.0, "max_points": 2.0,
                              "feedback": "Recommended Score: 2.", "citations": []}],
        "overall_feedback": "ok",
    })
    return model


def test_the_grading_node_sends_the_grading_rules():
    response, state = _short_answer_state()
    model = _model_for(response)
    grade_responses_node(state, gemini_pro=model)

    call = model.generate_deterministic.call_args
    assert call.kwargs["system_instruction"] == GRADING_SYSTEM_INSTRUCTION
    assert "temperature 0" in GRADING_SYSTEM_INSTRUCTION
    assert GRADING_SYSTEM_INSTRUCTION not in call.args[0]  # rules in the system channel, not the prompt
    assert call.args[0].startswith("SECURITY NOTICE")       # the prompt is unchanged
    model.generate.assert_not_called()


def test_the_same_submission_twice_sends_the_same_call_and_gets_the_same_score():
    response, state = _short_answer_state()
    model = _model_for(response)
    evidence = state["evidence_per_question"]
    outs = [grade_responses_node(grading_state([response], evidence=evidence), gemini_pro=model) for _ in range(2)]
    first, second = model.generate_deterministic.call_args_list
    assert first == second
    assert outs[0]["total_score"] == outs[1]["total_score"] == 2.0
    assert outs[0]["criterion_results"] == outs[1]["criterion_results"]


def test_closed_questions_still_make_no_model_call():
    model = MagicMock()
    mcq = grading_response(question_type="mcq", stem="Which?", answer_key={"correct_answer": "B"},
                           max_points=1.0, answer_choice="B")
    out = grade_responses_node(grading_state([mcq]), gemini_pro=model)
    assert out["total_score"] == 1.0
    model.generate_deterministic.assert_not_called()
