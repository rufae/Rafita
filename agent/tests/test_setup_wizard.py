"""Setup wizard tests (task 2.6)."""

import importlib.util
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup_wizard.py"


def _load():
    spec = importlib.util.spec_from_file_location("setup_wizard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wizard = _load()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_parse_and_upsert_env_preserves_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nTELEGRAM_TOKEN=old\nOTHER=1\n", encoding="utf-8")
    values = wizard.parse_env(env)
    assert values["TELEGRAM_TOKEN"] == "old"
    wizard.upsert_env(env, {"TELEGRAM_TOKEN": "new", "AI_PROVIDER": "ollama"})
    text = env.read_text(encoding="utf-8")
    assert "# comment" in text
    assert "TELEGRAM_TOKEN=new" in text
    assert "AI_PROVIDER=ollama" in text
    assert "OTHER=1" in text


def test_validate_vault_creates_directory(tmp_path):
    target = tmp_path / "vault"
    ok, message = wizard.validate_vault(target)
    assert ok and target.is_dir()
    assert "vault OK" in message


def test_check_local_provider_ok_and_missing(tmp_path, monkeypatch):
    payload = b'{"models":[{"name":"gemma4:12b"},{"name":"bge-m3:latest"}]}'
    monkeypatch.setattr(wizard.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload))
    ok, message = wizard.check_ollama("http://localhost:11434", "gemma4:12b", "bge-m3")
    assert ok, message

    ok, message = wizard.check_ollama("http://localhost:11434", "gemma4:12b", "nope")
    assert not ok and "faltan modelos" in message


def test_check_openai_rejects_bad_credentials(monkeypatch):
    def _raise(*args, **kwargs):
        raise urllib.error.HTTPError("http://x/models", 401, "unauthorized", {}, None)

    monkeypatch.setattr(wizard.urllib.request, "urlopen", _raise)
    ok, message = wizard.check_openai("http://localhost:9999/v1", "bad")
    assert not ok and "credenciales rechazadas" in message


def test_build_updates_maps_provider_config():
    class Args:
        token = "123:abc"
        provider = "openai"
        model = "gpt-4o-mini"
        embedding_model = "text-embedding-3-small"
        base_url = "https://api.example.com/v1"
        api_key = "sk-test"

    updates = wizard.build_updates(Args(), {})
    assert updates["AI_PROVIDER"] == "openai"
    assert updates["OPENAI_MODEL"] == "gpt-4o-mini"
    assert updates["OPENAI_EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert updates["TELEGRAM_TOKEN"] == "123:abc"
