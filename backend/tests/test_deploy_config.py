"""
tests/test_deploy_config.py
---------------------------
The Render blueprint sets the deployed backend's environment, which overrides
core/config.py. These pin the values that silently broke a deploy before.
Not a substitute for a real deploy: nothing here runs on Render.
"""

from pathlib import Path

import yaml

from core.config import Settings

_REPO = Path(__file__).resolve().parents[2]


def _render_env() -> dict[str, str]:
    blueprint = yaml.safe_load((_REPO / "render.yaml").read_text(encoding="utf-8"))
    backend = next(s for s in blueprint["services"] if s.get("name") == "tutorforge-backend")
    return {v["key"]: v.get("value") for v in backend["envVars"]}


def test_render_uses_the_configured_embedding_model():
    # text-embedding-004 is retired: with it every upload failed and the tutor
    # could answer nothing.
    assert _render_env()["EMBEDDING_MODEL"] == Settings.model_fields["embedding_model"].default
