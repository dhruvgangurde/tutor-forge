"""
tests/test_settings_env_file.py
-------------------------------
The suite must run on its defaults (CLAUDE.md invariant 6). Settings read
.env from the working directory, so under pytest a developer's backend/.env
(real keys, a real database URL, another provider) reached every Settings,
including the module singleton built at import.
"""

from core.config import Settings

_PLANTED = "planted-dummy-value-not-a-real-key"


def test_a_dotenv_in_the_working_directory_never_reaches_settings(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        f"GEMINI_API_KEY={_PLANTED}\nLLM_PROVIDER=ollama\nGROUNDEDNESS_THRESHOLD=0.99\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.gemini_api_key != _PLANTED
    assert s.llm_provider == Settings.model_fields["llm_provider"].default
    assert s.groundedness_threshold == Settings.model_fields["groundedness_threshold"].default
