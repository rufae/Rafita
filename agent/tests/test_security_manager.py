"""Tests for security_manager — fail-closed encryption and key persistence."""

import base64

import pytest
from cryptography.fernet import Fernet

from src import config
from src.config import settings
from src.utils import security_manager


@pytest.fixture
def isolated_env_file(tmp_path, monkeypatch):
    """Point security_manager at a throwaway env file and clear key state."""
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_TOKEN=dummy\n", encoding="utf-8")
    monkeypatch.setattr(security_manager, "ENV_PATH", env_path)
    monkeypatch.setattr(security_manager, "_cipher", None)
    monkeypatch.setattr(settings, "encryption_key", "")
    return env_path


def test_env_path_is_single_source_of_truth():
    """security_manager must write the same file Settings reads."""
    assert security_manager.ENV_PATH == config.ENV_FILE_PATH
    assert str(config.ENV_FILE_PATH) == settings.model_config["env_file"]


def test_invalid_configured_key_raises_not_plaintext(isolated_env_file, monkeypatch):
    """Audit regression: invalid key must raise, never return the plaintext value."""
    monkeypatch.setattr(settings, "encryption_key", "not-a-valid-fernet")
    with pytest.raises(ValueError):
        security_manager.encrypt_value("audit-marker")


def test_generated_key_is_persisted_and_reloadable(isolated_env_file, monkeypatch):
    """Encrypt, simulate restart (fresh state), decrypt against persisted key."""
    first = security_manager.get_or_create_encryption_key()
    persisted = isolated_env_file.read_text(encoding="utf-8")
    assert "ENCRYPTION_KEY=" in persisted

    token = security_manager.encrypt_value("super-secret")

    monkeypatch.setattr(security_manager, "_cipher", None)
    monkeypatch.setattr(settings, "encryption_key", "")
    assert security_manager.get_or_create_encryption_key() == first
    assert security_manager.decrypt_value(token) == "super-secret"


def test_persisted_key_survives_stale_empty_settings(isolated_env_file, monkeypatch):
    """Compose injects env vars at creation; after restart the env may be stale.

    The persisted key in the env file must win over an empty ENCRYPTION_KEY.
    """
    first = security_manager.get_or_create_encryption_key()
    token = security_manager.encrypt_value("secret-a")

    monkeypatch.setattr(security_manager, "_cipher", None)
    monkeypatch.setattr(settings, "encryption_key", "")

    second = security_manager.get_or_create_encryption_key()
    assert second == first
    assert security_manager.decrypt_value(token) == "secret-a"


def test_configured_key_takes_precedence_without_writing(isolated_env_file, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setattr(settings, "encryption_key", key.decode("utf-8"))
    assert security_manager.get_or_create_encryption_key() == key
    assert "ENCRYPTION_KEY=" not in isolated_env_file.read_text(encoding="utf-8")


def test_legacy_double_encoded_key_accepted_from_settings(isolated_env_file, monkeypatch):
    """The pre-0.1 code persisted base64(Fernet key); it must keep working."""
    key = Fernet.generate_key()
    legacy_value = base64.urlsafe_b64encode(key).decode("utf-8")
    monkeypatch.setattr(settings, "encryption_key", legacy_value)
    assert security_manager.get_or_create_encryption_key() == key


def test_legacy_credentials_decrypt_after_upgrade(isolated_env_file, monkeypatch):
    """Values encrypted by the old code must still decrypt (wire compatibility)."""
    key = Fernet.generate_key()
    legacy_file_value = base64.urlsafe_b64encode(key).decode("utf-8")
    isolated_env_file.write_text(
        "TELEGRAM_TOKEN=dummy\nENCRYPTION_KEY=%s\n" % legacy_file_value,
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "encryption_key", "")
    old_token = base64.urlsafe_b64encode(Fernet(key).encrypt(b"legacy-secret")).decode("utf-8")
    assert security_manager.decrypt_value(old_token) == "legacy-secret"


def test_unpersistable_key_raises_runtime_error(tmp_path, monkeypatch):
    """Fail-closed: no ephemeral in-memory key when the key cannot be persisted."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(security_manager, "ENV_PATH", blocker / "sub" / ".env")
    monkeypatch.setattr(security_manager, "_cipher", None)
    monkeypatch.setattr(settings, "encryption_key", "")
    with pytest.raises(RuntimeError):
        security_manager.encrypt_value("audit-marker")


def test_empty_plaintext_passthrough():
    assert security_manager.encrypt_value("") == ""
    assert security_manager.decrypt_value("") == ""


def test_webhook_secret_generated_and_persisted(isolated_env_file, monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "")
    first = security_manager.get_or_create_webhook_secret()
    assert len(first) >= 32
    assert ("WEBHOOK_SECRET=%s" % first) in isolated_env_file.read_text(encoding="utf-8")

    monkeypatch.setattr(settings, "webhook_secret", "")
    assert security_manager.get_or_create_webhook_secret() == first


def test_configured_webhook_secret_takes_precedence(isolated_env_file, monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "my-secret")
    assert security_manager.get_or_create_webhook_secret() == "my-secret"
    assert "WEBHOOK_SECRET=" not in isolated_env_file.read_text(encoding="utf-8")


def test_unpersistable_webhook_secret_returns_empty(tmp_path, monkeypatch):
    """Fail-closed: no persistence -> empty secret -> gateway rejects webhooks."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(security_manager, "ENV_PATH", blocker / "sub" / ".env")
    monkeypatch.setattr(settings, "webhook_secret", "")
    assert security_manager.get_or_create_webhook_secret() == ""
