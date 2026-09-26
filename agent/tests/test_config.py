"""Tests for Settings parsing, especially ADMIN_IDS (task 0.5)."""

from pathlib import Path

import pytest

from src.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)


def _settings_from(tmp_path: Path, extra: str = "") -> Settings:
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=dummy\n" + extra, encoding="utf-8")
    return Settings(_env_file=env)


def test_admin_ids_csv_format(tmp_path):
    settings = _settings_from(tmp_path, "ADMIN_IDS=123456789,987654321\n")
    assert settings.admin_ids == [123456789, 987654321]
    assert all(isinstance(v, int) for v in settings.admin_ids)


def test_admin_ids_json_format(tmp_path):
    settings = _settings_from(tmp_path, "ADMIN_IDS=[123456789, 42]\n")
    assert settings.admin_ids == [123456789, 42]


def test_admin_ids_empty_value(tmp_path):
    assert _settings_from(tmp_path, "ADMIN_IDS=\n").admin_ids == []


def test_admin_ids_missing_value(tmp_path):
    assert _settings_from(tmp_path).admin_ids == []


def test_ollama_request_timeout_default_and_override(tmp_path):
    assert _settings_from(tmp_path).ollama_request_timeout == 600
    settings = _settings_from(tmp_path, "OLLAMA_REQUEST_TIMEOUT=120\n")
    assert settings.ollama_request_timeout == 120


def test_whisper_cpu_threads_default_is_4(tmp_path):
    assert _settings_from(tmp_path).whisper_cpu_threads == 4


def test_whisper_cpu_threads_override_for_hp(tmp_path):
    settings = _settings_from(tmp_path, "WHISPER_CPU_THREADS=2\n")
    assert settings.whisper_cpu_threads == 2


def test_admin_ids_separators_and_spaces(tmp_path):
    assert _settings_from(tmp_path, "ADMIN_IDS=1; 2,3 4\n").admin_ids == [1, 2, 3, 4]


def test_admin_ids_single_value(tmp_path):
    assert _settings_from(tmp_path, "ADMIN_IDS=987\n").admin_ids == [987]


def test_admin_ids_invalid_entries_skipped(tmp_path):
    assert _settings_from(tmp_path, "ADMIN_IDS=123,abc,456\n").admin_ids == [123, 456]


def test_admin_ids_from_repo_env_example(tmp_path):
    """The committed .env.example must work with only TELEGRAM_TOKEN edited."""
    env = tmp_path / ".env"
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    content = content.replace("TELEGRAM_TOKEN=tu_token_aqui", "TELEGRAM_TOKEN=dummy")
    env.write_text(content, encoding="utf-8")
    settings = Settings(_env_file=env)
    assert settings.admin_ids == [123456789, 987654321]
