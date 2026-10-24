import os
import pytest
from services.api.app.config import load_env_file
from services.api.app.config import runtime_revision


def test_env_load_is_literal_and_override_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("FAMILYAGENT_MODEL", "old")
    file = tmp_path / ".env"
    file.write_text('FAMILYAGENT_MODEL="gemini-test"\nGEMINI_API_KEY="literal-$(whoami)" # comment\n')
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    load_env_file(file)
    assert os.environ["FAMILYAGENT_MODEL"] == "old"
    assert os.environ["GEMINI_API_KEY"] == "literal-$(whoami)"
    load_env_file(file, override=True)
    assert os.environ["FAMILYAGENT_MODEL"] == "gemini-test"


def test_bad_env_is_atomic_and_does_not_echo_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    file = tmp_path / ".env"
    file.write_text('GEMINI_API_KEY=synthetic-secret\nBROKEN="unclosed\n')
    with pytest.raises(ValueError) as exc:
        load_env_file(file, override=True)
    assert "synthetic-secret" not in str(exc.value)
    assert "GEMINI_API_KEY" not in os.environ


def test_runtime_revision_detects_code_and_web_build_changes(tmp_path):
    app = tmp_path / "services/api/app"
    app.mkdir(parents=True)
    (app / "intent.py").write_text("policy = 1")
    original = runtime_revision(tmp_path)
    assert original == runtime_revision(tmp_path)
    (app / "intent.py").write_text("policy = 2")
    changed = runtime_revision(tmp_path)
    assert changed != original
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text("new app assets")
    assert runtime_revision(tmp_path) != changed
