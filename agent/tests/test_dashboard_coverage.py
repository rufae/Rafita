"""Cobertura de handlers/dashboard.py (logs, claves, cerebro, resumen, escanear...)."""

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import settings
from src.handlers import dashboard


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_chat_action(self, *args, **kwargs):
        return None

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def make_update(user_id=12345):
    return SimpleNamespace(
        effective_message=FakeMessage(),
        effective_user=SimpleNamespace(id=user_id),
    )


def make_context(args=None):
    return SimpleNamespace(args=args or [])


class FakeDB:
    def __init__(self, **defaults):
        self.calls = []
        self.defaults = defaults

    async def get_all_chat_ids(self):
        self.calls.append("chat_ids")
        if "chat_ids_error" in self.defaults:
            raise RuntimeError("bd rota")
        return self.defaults.get("chat_ids", [])

    async def get_chat_history(self, chat_id, limit=50):
        return self.defaults.get("history", [])

    async def count_personal_knowledge(self, chat_id):
        return self.defaults.get("facts", 0)

    async def get_second_brain_stats(self):
        if "brain_error" in self.defaults:
            raise RuntimeError("sin stats")
        return self.defaults.get(
            "brain_stats",
            {"total_queries": 0, "recent_queries": []},
        )

    async def store_credential(self, chat_id, service, value):
        if "cred_error" in self.defaults:
            raise RuntimeError("sin ENCRYPTION_KEY")
        self.calls.append(("store", service, value))

    async def get_credential(self, chat_id, service):
        return self.defaults.get("credentials", {}).get(service)

    async def list_credentials(self, chat_id):
        return self.defaults.get("cred_list", [])

    async def delete_credential(self, chat_id, service):
        return self.defaults.get("cred_deleted", False)


def install_db(monkeypatch, **defaults):
    fake_db = FakeDB(**defaults)
    monkeypatch.setattr(dashboard, "db", fake_db)
    return fake_db


async def vstats_ok():
    return {"total_chunks": 12, "total_documents": 4}


async def vstats_error():
    raise RuntimeError("chroma caido")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "obsidian_vault"
    root.mkdir()
    real_path = pathlib.Path

    def remapped(*args, **kwargs):
        if args and str(args[0]) == "/data/obsidian_vault":
            return root
        return real_path(*args, **kwargs)

    monkeypatch.setattr(dashboard, "Path", remapped)
    return root


# ---------------------------------------------------------------------------
# _esc y _format_size
# ---------------------------------------------------------------------------


def test_esc_escapes_special_characters():
    assert dashboard._esc("a<b>&\"'") == "a&lt;b&gt;&amp;&quot;&#x27;"


def test_format_size_units():
    assert dashboard._format_size(10) == "10 B"
    assert dashboard._format_size(10 * 1024) == "10.0 KB"
    assert dashboard._format_size(10 * 1024 * 1024) == "10.0 MB"
    assert dashboard._format_size(10 * 1024 * 1024 * 1024) == "10.0 GB"


# ---------------------------------------------------------------------------
# logs_command
# ---------------------------------------------------------------------------


def install_admin(monkeypatch, user_id=12345):
    monkeypatch.setattr(settings, "admin_ids", [user_id])


async def test_logs_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    update = make_update(user_id=12345)

    await dashboard.logs_command(update, make_context())

    text, _kwargs = update.effective_message.replies[0]
    assert "Solo los administradores" in text


async def test_logs_without_lines(monkeypatch):
    install_admin(monkeypatch)
    monkeypatch.setattr(dashboard, "tail_logs", lambda n: [])
    update = make_update()

    await dashboard.logs_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "No hay logs disponibles" in text


async def test_logs_returns_requested_lines(monkeypatch):
    install_admin(monkeypatch)
    captured = {}

    def fake_tail(n):
        captured["n"] = n
        return ["linea1", "linea2"]

    monkeypatch.setattr(dashboard, "tail_logs", fake_tail)
    update = make_update()

    await dashboard.logs_command(update, make_context(["7"]))

    assert captured["n"] == 7
    text, kwargs = update.effective_message.replies[0]
    assert kwargs["parse_mode"] == "HTML"
    assert "<pre>" in text
    assert "linea1" in text


async def test_logs_clamps_and_defaults(monkeypatch):
    install_admin(monkeypatch)
    sizes = []

    def fake_tail(n):
        sizes.append(n)
        return ["x"]

    monkeypatch.setattr(dashboard, "tail_logs", fake_tail)
    update = make_update()

    await dashboard.logs_command(update, make_context(["999"]))
    await dashboard.logs_command(update, make_context(["0"]))
    await dashboard.logs_command(update, make_context(["no-numero"]))
    await dashboard.logs_command(update, make_context([]))

    assert sizes == [200, 1, 50, 50]


async def test_logs_truncates_long_body(monkeypatch):
    install_admin(monkeypatch)
    monkeypatch.setattr(dashboard, "tail_logs", lambda n: ["A" * 5000])
    update = make_update()

    await dashboard.logs_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    body = text.split("<pre>", 1)[1].rsplit("</pre>", 1)[0]
    assert len(body) == 3500


# ---------------------------------------------------------------------------
# guardar_clave / clave / claves / borrar_clave
# ---------------------------------------------------------------------------


async def test_guardar_clave_usage(monkeypatch):
    install_db(monkeypatch)
    update = make_update()

    await dashboard.guardar_clave_command(update, make_context(["solo"]))

    text, kwargs = update.effective_message.replies[0]
    assert "Uso:" in text
    assert kwargs["parse_mode"] == "Markdown"


async def test_guardar_clave_stores_value(monkeypatch):
    fake_db = install_db(monkeypatch)
    update = make_update()

    await dashboard.guardar_clave_command(update, make_context(["Gemini", "AIza", "xxx"]))

    assert ("store", "gemini", "AIza xxx") in fake_db.calls
    text, _kwargs = update.effective_message.replies[0]
    assert "Clave guardada" in text


async def test_guardar_clave_reports_crypto_errors(monkeypatch):
    install_db(monkeypatch, cred_error=True)
    update = make_update()

    await dashboard.guardar_clave_command(update, make_context(["gemini", "valor"]))

    text, _kwargs = update.effective_message.replies[0]
    assert "No se pudo cifrar" in text
    assert "ENCRYPTION_KEY" in text


async def test_clave_usage_and_missing(monkeypatch):
    install_db(monkeypatch)
    update = make_update()

    await dashboard.clave_command(update, make_context([]))
    text, _kwargs = update.effective_message.replies[0]
    assert "Uso:" in text

    update = make_update()
    await dashboard.clave_command(update, make_context(["gemini"]))
    text, _kwargs = update.effective_message.replies[0]
    assert "No hay clave guardada" in text


async def test_clave_masks_long_value(monkeypatch):
    install_db(monkeypatch, credentials={"gemini": "AIza-valor-largo-123"})
    update = make_update()

    await dashboard.clave_command(update, make_context(["Gemini"]))

    text, _kwargs = update.effective_message.replies[0]
    assert "AIza...-123" in text
    assert "AIza-valor-largo-123" not in text


async def test_clave_masks_short_value(monkeypatch):
    install_db(monkeypatch, credentials={"wifi": "corta"})
    update = make_update()

    await dashboard.clave_command(update, make_context(["wifi"]))

    text, _kwargs = update.effective_message.replies[0]
    assert "`***`" in text


async def test_claves_lists_services(monkeypatch):
    install_db(
        monkeypatch,
        cred_list=[{"service": "gemini", "updated_at": "2026-09-01 10:00:00"}],
    )
    update = make_update()

    await dashboard.claves_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "gemini" in text
    assert "2026-09-01" in text
    assert "/borrar_clave" in text


async def test_claves_empty(monkeypatch):
    install_db(monkeypatch, cred_list=[])
    update = make_update()

    await dashboard.claves_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "No tienes claves guardadas" in text


async def test_borrar_clave(monkeypatch):
    install_db(monkeypatch, cred_deleted=True)
    update = make_update()

    await dashboard.borrar_clave_command(update, make_context(["gemini"]))
    text, _kwargs = update.effective_message.replies[0]
    assert "eliminada" in text

    install_db(monkeypatch, cred_deleted=False)
    update = make_update()
    await dashboard.borrar_clave_command(update, make_context(["gemini"]))
    text, _kwargs = update.effective_message.replies[0]
    assert "No se encontró" in text


async def test_borrar_clave_usage(monkeypatch):
    install_db(monkeypatch)
    update = make_update()

    await dashboard.borrar_clave_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "Uso:" in text


# ---------------------------------------------------------------------------
# cerebro_command
# ---------------------------------------------------------------------------


async def test_cerebro_command_full_panel(vault, monkeypatch):
    nota = vault / "Proyectos" / "idea.md"
    nota.parent.mkdir(parents=True)
    nota.write_text("---\ntype: proyecto\n---\n# Idea", encoding="utf-8")
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    install_db(
        monkeypatch,
        brain_stats={
            "total_queries": 3,
            "recent_queries": [
                {"query_text": "mis coches", "chunks_retrieved": 2, "top_relevance": 0.9}
            ],
        },
    )
    update = make_update()

    await dashboard.cerebro_command(update, make_context([]))

    text, kwargs = update.effective_message.replies[0]
    assert kwargs["parse_mode"] == "Markdown"
    assert "SEGUNDO CEREBRO" in text
    assert "Proyectos" in text
    assert "Chunks indexados: 12" in text
    assert "Chunks por nota: 3.0" in text
    assert "mis coches" in text
    assert "90%" in text


async def test_cerebro_command_reports_errors(monkeypatch):
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_error))
    install_db(monkeypatch, brain_error=True)
    update = make_update()

    await dashboard.cerebro_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "Error:" in text
    assert "sin stats" in text


# ---------------------------------------------------------------------------
# escanear_command
# ---------------------------------------------------------------------------


async def test_escanear_success_with_results(monkeypatch):
    install_db(monkeypatch)

    async def fake_scan(user_id, since=None, limit=50):
        return {
            "success": True,
            "messages_scanned": 2,
            "extracted": [{"type": "llm_response", "content": "Encontré tu Audi A3"}],
        }

    import src.utils.message_scanner as scanner

    monkeypatch.setattr(scanner, "scan_messages", fake_scan)
    update = make_update()

    await dashboard.escanear_command(update, make_context(["2026-09-01"]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("Escaneando mensajes" in t for t in texts)
    assert any("Escaneo completado" in t for t in texts)
    assert any("Encontré tu Audi A3" in t for t in texts)


async def test_escanear_without_new_messages(monkeypatch):
    install_db(monkeypatch)

    async def fake_scan(user_id, since=None, limit=50):
        return {"success": True, "messages_scanned": 0, "extracted": []}

    import src.utils.message_scanner as scanner

    monkeypatch.setattr(scanner, "scan_messages", fake_scan)
    update = make_update()

    await dashboard.escanear_command(update, make_context([]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("No se encontraron mensajes nuevos" in t for t in texts)


async def test_escanear_failure(monkeypatch):
    install_db(monkeypatch)

    async def fake_scan(user_id, since=None, limit=50):
        return {"success": False, "message": "BD no disponible"}

    import src.utils.message_scanner as scanner

    monkeypatch.setattr(scanner, "scan_messages", fake_scan)
    update = make_update()

    await dashboard.escanear_command(update, make_context([]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("BD no disponible" in t for t in texts)


async def test_escanear_reports_exceptions(monkeypatch):
    install_db(monkeypatch)

    async def fake_scan(user_id, since=None, limit=50):
        raise RuntimeError("scanner roto")

    import src.utils.message_scanner as scanner

    monkeypatch.setattr(scanner, "scan_messages", fake_scan)
    update = make_update()

    await dashboard.escanear_command(update, make_context([]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("Error al escanear" in t for t in texts)


# ---------------------------------------------------------------------------
# resumen_command
# ---------------------------------------------------------------------------


async def test_resumen_command_with_vault(vault, monkeypatch):
    nota = vault / "Zettelkasten" / "nota.md"
    nota.parent.mkdir(parents=True)
    nota.write_text("---\ntype: nota-atomica\ntags: [coches, salud]\n---\n# Nota", encoding="utf-8")
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    install_db(
        monkeypatch,
        brain_stats={
            "total_queries": 1,
            "recent_queries": [{"query_text": "que coches tengo", "chunks_retrieved": 2}],
        },
    )
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        return "1. *Tamaño del cerebro*: 1 nota"

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)
    update = make_update()

    await dashboard.resumen_command(update, make_context([]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("Analizando tu segundo cerebro" in t for t in texts)
    assert any("Tamaño del cerebro" in t for t in texts)
    assert update.effective_message.replies[-1][1]["parse_mode"] == "Markdown"


async def test_resumen_command_llm_failure(monkeypatch):
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_error))
    install_db(monkeypatch, brain_error=True)
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        raise RuntimeError("llm caido")

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)
    update = make_update()

    await dashboard.resumen_command(update, make_context([]))

    texts = [t for t, _ in update.effective_message.replies]
    assert any("Error generando resumen" in t for t in texts)


# ---------------------------------------------------------------------------
# recordar_command
# ---------------------------------------------------------------------------


async def test_recordar_without_context_history(monkeypatch):
    install_db(monkeypatch, history=[])
    update = make_update()

    await dashboard.recordar_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "No hay conversacion reciente" in text


async def test_recordar_saves_note(monkeypatch):
    install_db(monkeypatch)
    saved = {}
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        return '"Mi nota recordada"'

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)

    async def fake_create_or_append(title, content, folder=""):
        saved["title"] = title
        saved["content"] = content
        saved["folder"] = folder
        return {"success": True, "filepath": "/vault/05-Zettelkasten/nota.md"}

    import src.utils.obsidian_manager as obsidian_manager

    monkeypatch.setattr(obsidian_manager, "create_or_append_note", fake_create_or_append)
    update = make_update()

    await dashboard.recordar_command(update, make_context(["el", "Audi", "A3"]))

    assert saved["title"] == "Mi_nota_recordada"
    assert "Audi A3" in saved["content"]
    assert "nota-atomica" in saved["content"]
    text, kwargs = update.effective_message.replies[-1]
    assert "Guardado en tu segundo cerebro" in text
    assert kwargs["parse_mode"] == "Markdown"


async def test_recordar_uses_recent_history(monkeypatch):
    install_db(
        monkeypatch,
        history=[
            {"role": "user", "content": "tengo un Audi A3"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "es azul"},
        ],
    )
    saved = {}
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        return "Titulo"

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)

    async def fake_create_or_append(title, content, folder=""):
        saved["content"] = content
        return {"success": True, "filepath": "/vault/n.md"}

    import src.utils.obsidian_manager as obsidian_manager

    monkeypatch.setattr(obsidian_manager, "create_or_append_note", fake_create_or_append)
    update = make_update()

    await dashboard.recordar_command(update, make_context([]))

    assert "tengo un Audi A3" in saved["content"]
    assert "es azul" in saved["content"]


async def test_recordar_title_fallback_on_llm_error(monkeypatch):
    install_db(monkeypatch)
    saved = {}
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        raise RuntimeError("llm caido")

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)

    async def fake_create_or_append(title, content, folder=""):
        saved["title"] = title
        return {"success": True, "filepath": "/vault/n.md"}

    import src.utils.obsidian_manager as obsidian_manager

    monkeypatch.setattr(obsidian_manager, "create_or_append_note", fake_create_or_append)
    update = make_update()

    await dashboard.recordar_command(update, make_context(["el", "Audi"]))

    assert "Audi" in saved["title"]


async def test_recordar_reports_save_errors(monkeypatch):
    install_db(monkeypatch)
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        return "Titulo"

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)

    async def fake_create_or_append(title, content, folder=""):
        return {"success": False, "message": "vault llena"}

    import src.utils.obsidian_manager as obsidian_manager

    monkeypatch.setattr(obsidian_manager, "create_or_append_note", fake_create_or_append)
    update = make_update()

    await dashboard.recordar_command(update, make_context(["tema"]))

    text, _kwargs = update.effective_message.replies[-1]
    assert "Error al guardar: vault llena" in text


# ---------------------------------------------------------------------------
# status_command: ramas no cubiertas por test_status_command.py
# ---------------------------------------------------------------------------


async def test_status_health_degraded_and_unhealthy(monkeypatch):
    install_db(monkeypatch, chat_ids=[])
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(
            get_system_health=AsyncMock(return_value={"logs": {"recent_errors": ["boom"]}})
        ),
    )

    for health, expected in (
        ({"status": "degraded", "provider": "openai", "detail": "modelo sin cargar"}, "degradado"),
        ({"status": "down", "provider": "openai", "detail": "backend caido"}, "backend caido"),
    ):

        async def fake_check_health(_health=health):
            return _health

        monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
        update = make_update()
        await dashboard.status_command(update, make_context([]))
        text = update.effective_message.replies[0][0]
        assert expected in text
        assert "Errores recientes: 1" in text


async def test_status_vault_and_db_details(vault, monkeypatch):
    nota = vault / "Inbox" / "n.md"
    nota.parent.mkdir()
    nota.write_text("# hola", encoding="utf-8")

    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_error))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(return_value={"logs": {"status": "sin logs"}})),
    )
    install_db(monkeypatch, chat_ids=[1, 2], history=[{"role": "user", "content": "hola"}], facts=5)
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    text = update.effective_message.replies[0][0]
    assert "Chats activos: 2" in text
    assert "Hechos personales: 5" in text
    assert "Notas .md: 1" in text
    assert "Subcarpetas: 1" in text
    assert "Chunks indexados" not in text
    assert "Error: " in text


async def test_status_db_error_reported(monkeypatch):
    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(return_value={"logs": {"recent_errors": []}})),
    )
    install_db(monkeypatch, chat_ids_error=True)
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    text = update.effective_message.replies[0][0]
    assert "Error leyendo BD" in text
    assert "Sin errores" in text


async def test_status_chunks_messages(monkeypatch):
    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(return_value={"logs": {"status": "n/a"}})),
    )
    install_db(monkeypatch, chat_ids=[])
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    # El panel se trocea para no romper el limite de mensajes de Telegram
    assert update.effective_message.replies
    for text, kwargs in update.effective_message.replies:
        assert kwargs.get("parse_mode") == "HTML"
        assert len(text) <= 4096
    assert "PANEL DE CONTROL" in update.effective_message.replies[0][0]


# ---------------------------------------------------------------------------
# guards de mensaje ausente y ramas menores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        dashboard.status_command,
        dashboard.logs_command,
        dashboard.cerebro_command,
        dashboard.escanear_command,
        dashboard.guardar_clave_command,
        dashboard.clave_command,
        dashboard.claves_command,
        dashboard.borrar_clave_command,
        dashboard.resumen_command,
        dashboard.recordar_command,
    ],
)
async def test_commands_without_message_are_noop(monkeypatch, command):
    install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None
    update.effective_user = SimpleNamespace(id=1)

    await command(update, make_context([]))
    # El guard "sin mensaje" sale sin tocar la API de Telegram


async def test_status_reports_db_file_size(monkeypatch, tmp_path):
    db_file = tmp_path / "rafita.db"
    db_file.write_bytes(b"x" * 2048)
    monkeypatch.setattr(settings, "db_path", str(db_file))

    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(return_value={"logs": {"recent_errors": []}})),
    )
    install_db(monkeypatch, chat_ids=[])
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    text = update.effective_message.replies[0][0]
    assert "Tamaño: 2.0 KB" in text


async def test_cerebro_without_queries_hint(monkeypatch):
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    install_db(
        monkeypatch,
        brain_stats={"total_queries": 0, "recent_queries": []},
    )
    update = make_update()

    await dashboard.cerebro_command(update, make_context([]))

    text, _kwargs = update.effective_message.replies[0]
    assert "Aun no se han hecho consultas" in text


async def test_resumen_skips_ignored_dirs_and_bad_notes(vault, monkeypatch):
    ignored = vault / "templates" / "plantilla.md"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("---\ntype: template\ntags: [uno, uno, dos]\n---\n# P", encoding="utf-8")
    broken = vault / "Inbox" / "rota.md"
    broken.parent.mkdir()
    broken.write_text("---\ntype: nota\ntags: [tres, tres, cuatro]\n---\n# R", encoding="utf-8")
    # Un directorio con extension .md hace fallar la lectura y se ignora
    (vault / "Inbox" / "ilegible.md").mkdir()
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    install_db(monkeypatch)
    captured = {}
    fake_llm = MagicMock()

    async def fake_chat(**kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        return "resumen ok"

    fake_llm.chat = fake_chat
    import src.ollama_client as ollama_client

    monkeypatch.setattr(ollama_client, "llm", fake_llm)
    update = make_update()

    await dashboard.resumen_command(update, make_context([]))

    # La carpeta templates (ignorada) no entra en los datos del resumen
    assert "plantilla.md" not in captured["user"]
    assert "rota.md" in captured["user"]
    # El tag repetido solo cuenta una vez para el primero
    assert "tres" in captured["user"]


async def test_status_tolerates_disk_and_log_probe_errors(monkeypatch):
    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard.shutil,
        "disk_usage",
        MagicMock(side_effect=PermissionError("sin permiso")),
    )
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(side_effect=RuntimeError("workspace caido"))),
    )
    install_db(monkeypatch, chat_ids=[])
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    text = update.effective_message.replies[0][0]
    assert "PANEL DE CONTROL" in text
    assert "workspace caido" in text
    assert "Disco" in text


async def test_status_voice_section_when_stt_tts_missing(monkeypatch, tmp_path):
    async def fake_check_health():
        return {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1}

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard, "vector_db", SimpleNamespace(get_stats=vstats_ok))
    monkeypatch.setattr(
        dashboard,
        "wm",
        SimpleNamespace(get_system_health=AsyncMock(return_value={"logs": {"recent_errors": []}})),
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "piper", None)
    install_db(monkeypatch, chat_ids=[])
    update = make_update()

    await dashboard.status_command(update, make_context([]))

    text = update.effective_message.replies[0][0]
    assert "STT (Whisper): ❌ no instalado" in text
    assert "TTS (Piper): ❌ no instalado" in text
