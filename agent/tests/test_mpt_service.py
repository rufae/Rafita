"""Tests del puente SSH de MoneyPrinterTurbo (tool hacer_videos)."""

import asyncio

import pytest

import src.services.mpt_service as mpt
from src.config import settings


def _run(coro):
    return asyncio.run(coro)


def test_sanitiza_guion_valido():
    assert mpt._sanitizar_guion("uno.md") == "uno.md"
    assert mpt._sanitizar_guion("guiones/El_cachorro_perdido.md") == "El_cachorro_perdido.md"
    assert mpt._sanitizar_guion("  guiones/  uno.md  ") == "uno.md"


@pytest.mark.parametrize(
    "malo",
    ["../secreto.md", "a/b/../c.md", "guion.txt", ".oculto.md", "", "   ", "x" * 130 + ".md"],
)
def test_rechaza_guion_invalido(malo):
    with pytest.raises(ValueError):
        mpt._sanitizar_guion(malo)


def test_comando_lanzar_sin_fuerza_y_con_guion(monkeypatch):
    monkeypatch.setattr(settings, "mpt_dir", "/opt/mpt")
    cmd = mpt._construir_comando_lanzar("uno.md", fuerza=False)
    assert "cd /opt/mpt" in cmd
    assert "hacer_videos.py uno.md" in cmd
    assert "--fuerza" not in cmd
    assert "nohup" in cmd
    assert mpt.LOG_REMOTO in cmd
    assert "LANZADO_PID=" in cmd


def test_comando_lanzar_fuerza(monkeypatch):
    monkeypatch.setattr(settings, "mpt_dir", "/opt/mpt")
    cmd = mpt._construir_comando_lanzar("", fuerza=True)
    assert "--fuerza" in cmd
    assert "uno.md" not in cmd


def test_lanzar_sin_configurar_devuelve_error(monkeypatch):
    monkeypatch.setattr(settings, "mpt_ssh_target", "")
    resultado = _run(mpt.ejecutar_hacer_videos(guion="uno.md"))
    assert resultado["success"] is False
    assert "MPT_SSH_TARGET" in resultado["message"]


def test_lanzar_accion_invalida(monkeypatch):
    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    resultado = _run(mpt.ejecutar_hacer_videos(accion="borrar"))
    assert resultado["success"] is False
    assert "accion" in resultado["message"]


def test_lanzar_guion_invalido_no_abre_ssh(monkeypatch):
    llamadas = []

    def _falso_ssh(comando):
        llamadas.append(comando)
        return "LANZADO_PID=1"

    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", _falso_ssh)
    resultado = _run(mpt.ejecutar_hacer_videos(guion="../../etc/passwd.md"))
    assert resultado["success"] is False
    assert llamadas == []


def test_lanzar_ok(monkeypatch):
    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", lambda comando: "LANZADO_PID=4242")
    resultado = _run(mpt.ejecutar_hacer_videos(guion="guiones/uno.md", fuerza=True))
    assert resultado["success"] is True
    assert resultado["pid"] == "4242"
    assert "Telegram" in resultado["message"]


def test_lanzar_sin_confirmacion(monkeypatch):
    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", lambda comando: "")
    resultado = _run(mpt.ejecutar_hacer_videos())
    assert resultado["success"] is False


def test_estado_corriendo(monkeypatch):
    salida = "ESTADO=CORRIENDO\nlinea 1 del log\nlinea 2 del log"
    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", lambda comando: salida)
    resultado = _run(mpt.ejecutar_hacer_videos(accion="estado"))
    assert resultado["success"] is True
    assert resultado["corriendo"] is True
    assert "Corriendo ahora" in resultado["message"]
    assert "linea 2 del log" in resultado["message"]
    assert "ESTADO=" not in resultado["message"].split("\n", 1)[-1]


def test_estado_parado_sin_log(monkeypatch):
    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", lambda comando: "ESTADO=PARADO\n(sin log todavia)")
    resultado = _run(mpt.ejecutar_hacer_videos(accion="estado"))
    assert resultado["success"] is True
    assert resultado["corriendo"] is False
    assert "Parado" in resultado["message"]


def test_estado_propaga_error_ssh(monkeypatch):
    def _roto(comando):
        raise RuntimeError("SSH fallo (codigo 255): conexion rechazada")

    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", _roto)
    resultado = _run(mpt.ejecutar_hacer_videos(accion="estado"))
    assert resultado["success"] is False
    assert "conexion rechazada" in resultado["message"]


def test_cd_con_tilde_no_se_comilla(monkeypatch):
    monkeypatch.setattr(settings, "mpt_dir", "~/PROYECTOS/MoneyPrinterTurbo")
    assert mpt._cd() == "cd ~/PROYECTOS/MoneyPrinterTurbo"


def test_cd_ruta_normal_se_comilla(monkeypatch):
    monkeypatch.setattr(settings, "mpt_dir", "/opt/mi carpeta")
    assert mpt._cd() == "cd '/opt/mi carpeta'"


def test_ssh_deriva_known_hosts_de_la_clave(monkeypatch):
    capturado = {}

    class _R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kw):
        capturado["cmd"] = cmd
        capturado["kw"] = kw
        return _R()

    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(settings, "mpt_ssh_key", "/data/mpt_ssh/id_ed25519")
    monkeypatch.setattr(mpt.subprocess, "run", fake_run)
    assert mpt._ssh("echo hola") == "ok"
    arg = [a for a in capturado["cmd"] if a.startswith("UserKnownHostsFile=")][0]
    assert arg == "UserKnownHostsFile=/data/mpt_ssh/known_hosts"
    assert capturado["kw"].get("encoding") == "utf-8"


def test_execute_tool_fuerza_string_falso(monkeypatch):
    from src.handlers.chat import _execute_tool

    comandos = []

    def _falso_ssh(comando):
        comandos.append(comando)
        return "LANZADO_PID=7"

    monkeypatch.setattr(settings, "mpt_ssh_target", "user@host")
    monkeypatch.setattr(mpt, "_ssh", _falso_ssh)
    resultado = asyncio.run(
        _execute_tool(1, "hacer_videos", {"fuerza": "false", "guion": "uno.md"})
    )
    assert resultado["success"] is True
    assert "--fuerza" not in comandos[0]
