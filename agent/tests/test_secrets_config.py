"""Tests de secretos file-based en config (mejora 7)."""

import pytest

import src.config as config


@pytest.fixture
def sin_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE_PATH", tmp_path / "no-existe.env")


def test_secret_file_rellena_variable_vacia(sin_env_file, tmp_path):
    fichero = tmp_path / "telegram.txt"
    fichero.write_text("secreto-desde-fichero\n", encoding="utf-8")
    entorno: dict[str, str] = {"TELEGRAM_TOKEN_FILE": str(fichero)}
    config._apply_secret_files(entorno)
    assert entorno["TELEGRAM_TOKEN"] == "secreto-desde-fichero"


def test_variable_de_entorno_tiene_prioridad(sin_env_file, tmp_path):
    fichero = tmp_path / "telegram.txt"
    fichero.write_text("del-fichero", encoding="utf-8")
    entorno = {"TELEGRAM_TOKEN": "del-entorno", "TELEGREM_TOKEN_FILE": str(fichero)}
    entorno["TELEGRAM_TOKEN_FILE"] = str(fichero)
    config._apply_secret_files(entorno)
    assert entorno["TELEGRAM_TOKEN"] == "del-entorno"


def test_env_file_tiene_prioridad_sobre_fichero(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=del-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE_PATH", env_file)
    fichero = tmp_path / "telegram.txt"
    fichero.write_text("del-fichero", encoding="utf-8")
    entorno = {"TELEGRAM_TOKEN_FILE": str(fichero)}
    config._apply_secret_files(entorno)
    assert "TELEGRAM_TOKEN" not in entorno


def test_fichero_ilevible_es_error_duro(sin_env_file, tmp_path):
    entorno = {"TELEGRAM_TOKEN_FILE": str(tmp_path / "no-existe.txt")}
    with pytest.raises(RuntimeError):
        config._apply_secret_files(entorno)


def test_fichero_vacio_no_rellena(sin_env_file, tmp_path):
    fichero = tmp_path / "vacio.txt"
    fichero.write_text("   \n", encoding="utf-8")
    entorno = {"TELEGRAM_TOKEN_FILE": str(fichero)}
    config._apply_secret_files(entorno)
    assert "TELEGRAM_TOKEN" not in entorno


def test_todos_los_secretos_soportados_tienen_rama():
    assert "TELEGRAM_TOKEN" in config._SECRET_ENVS
    assert "WEBHOOK_SECRET" in config._SECRET_ENVS
    assert "VOICE_CALL_TOKEN" in config._SECRET_ENVS
