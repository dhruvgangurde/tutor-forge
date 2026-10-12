"""
tests/test_deploy_config.py
---------------------------
The Render blueprint sets the deployed backend's environment, which overrides
core/config.py. These pin the values that silently broke a deploy before.
Not a substitute for a real deploy: nothing here runs on Render.
"""

import re
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


def test_render_trusts_the_proxy_for_the_client_address():
    # Behind Render's proxy every visitor otherwise shares one address, so ten
    # sign-ins a minute, site-wide, locked everyone out (core/ratelimit.py).
    assert _render_env()["TRUSTED_PROXY"] == "true"


def test_docker_start_command_never_trusts_every_forwarder():
    # With --forwarded-allow-ips="*" uvicorn takes the LEFT-most X-Forwarded-For
    # entry, which the client wrote, as the client address. TRUSTED_PROXY alone
    # is what the rate limiter needs (it reads the entry the proxy appended).
    dockerfile = (_REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert not re.search(r"forwarded-allow-ips=['\"]?\*", cmd)
