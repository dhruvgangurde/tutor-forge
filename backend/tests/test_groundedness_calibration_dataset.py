"""
tests/test_groundedness_calibration_dataset.py
----------------------------------------------
Watch items in eval/datasets/groundedness_calibration.json: probes whose live
reply needs a person to look at it on the next live run. Reported, never
gated (nothing in the eval scores a "watch").
"""

import json
from pathlib import Path

_DATASET = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "groundedness_calibration.json"


def _probes() -> dict[str, dict]:
    return {p["id"]: p for p in json.loads(_DATASET.read_text(encoding="utf-8"))["probes"]}


def test_the_two_live_replies_to_watch_are_recorded():
    probes = _probes()
    for pid in ("adjacent-03", "adv-03"):
        assert probes[pid].get("watch", "").strip(), pid


def test_a_watch_item_carries_its_live_result():
    for pid, probe in _probes().items():
        if "watch" in probe:
            assert probe.get("live") is True and probe.get("live_outcome"), pid


def test_hash_collisions_count_as_grounded():
    # Rabin-Karp (p. 22) teaches hash collisions: decided 2026-10-12.
    assert _probes()["adjacent-03"]["label"] == "grounded"
