"""Detección autónoma de tareas y compromisos (tareas.md ítems 6 y 7)."""

import json
from datetime import UTC, datetime, timedelta

from src.services import automation_service as auto


class _LLMFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = 0
        self.contenidos = []

    async def chat(self, **kwargs):
        self.llamadas += 1
        self.contenidos.append(str(kwargs))
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


class _GoogleFalso:
    def __init__(self, ready=False, mails=None):
        self.is_ready = ready
        self.mails = mails or []
        self.creadas = []

    async def initialize(self):
        return self.is_ready

    async def search_gmail(self, query, max_results=5):
        return {"success": True, "messages": self.mails}

    async def create_task(self, title, tasklist="@default", due=None):
        self.creadas.append((title, due))
        return {"success": True, "id": "g1"}


def _db_falso(monkeypatch, chats=None, kv=None):
    store = {"kv": dict(kv or {}), "tareas": [], "alertas": []}

    async def kv_get(k):
        return store["kv"].get(k)

    async def kv_set(k, v, expires_at=None):
        store["kv"][k] = v

    async def get_all_chat_ids():
        return [900000001]

    async def get_chat_history(chat_id, limit=50, offset=0):
        return list(chats or [])

    async def add_task(chat_id, title, due=None):
        store["tareas"].append((title, due))
        return len(store["tareas"])

    async def add_alert(chat_id, message, alert_type="info", expires_at=None):
        store["alertas"].append((message, expires_at, alert_type))
        return len(store["alertas"])

    for nombre, fn in (
        ("kv_get", kv_get),
        ("kv_set", kv_set),
        ("get_all_chat_ids", get_all_chat_ids),
        ("get_chat_history", get_chat_history),
        ("add_task", add_task),
        ("add_alert", add_alert),
    ):
        monkeypatch.setattr("src.database.db.%s" % nombre, fn)
    return store


def _chat_reciente(texto):
    return {
        "role": "user",
        "content": texto,
        "created_at": (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _bot_falso(monkeypatch):
    enviados = []

    class _Bot:
        async def send_proactive_message(self, chat_id, text):
            enviados.append((chat_id, text))

    monkeypatch.setattr("src.bot.bot", _Bot())
    return enviados


def _deteccion(titulo, fecha, confianza="alta", tipo="tarea", fuente="chat"):
    return json.dumps(
        {
            "detecciones": [
                {
                    "tipo": tipo,
                    "titulo": titulo,
                    "fecha": fecha,
                    "fuente": fuente,
                    "confianza": confianza,
                    "evidencia": "cita de prueba",
                }
            ]
        },
        ensure_ascii=False,
    )


def _google(monkeypatch, google):
    monkeypatch.setattr("src.services.google_services_manager.google_services", google)
    return google


async def test_crea_tarea_local_desde_chat(monkeypatch):
    store = _db_falso(
        monkeypatch,
        chats=[_chat_reciente("Tengo que renovar el dominio el 2026-10-15")],
    )
    _google(monkeypatch, _GoogleFalso(ready=False))
    enviados = _bot_falso(monkeypatch)
    llm = _LLMFalso(_deteccion("Renovar el dominio", "2026-10-15"))
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments(force=True)

    assert res["success"] is True
    assert store["tareas"] == [("Renovar el dominio", "2026-10-15")]
    assert store["kv"]["commitments:last"]
    assert enviados and "dominio" in enviados[0][1]
    assert res["fuentes"]["chats"] == 1


async def test_dedup_no_repite_la_misma_tarea(monkeypatch):
    store = _db_falso(monkeypatch, chats=[_chat_reciente("enviar informe el viernes")])
    _google(monkeypatch, _GoogleFalso(ready=False))
    _bot_falso(monkeypatch)
    llm = _LLMFalso(_deteccion("Enviar informe", "2026-10-09"))
    monkeypatch.setattr("src.ollama_client.llm", llm)

    await auto.detect_commitments(force=True)
    res2 = await auto.detect_commitments(force=True)

    assert len(store["tareas"]) == 1
    assert res2["creadas"] == []
    assert llm.llamadas == 2  # force reanaliza, pero el dedup evita crear


async def test_gate_diario_sin_force(monkeypatch):
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    hoy = _dt.now(_UTC).strftime("%Y-%m-%d")
    store = _db_falso(monkeypatch, kv={"commitments:last": hoy})
    llm = _LLMFalso(_deteccion("X", None))
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments()

    assert res.get("skipped")
    assert llm.llamadas == 0
    assert store["tareas"] == []


async def test_llm_caido_es_honesto_y_no_guarda_gate(monkeypatch):
    store = _db_falso(monkeypatch, chats=[_chat_reciente("algo")])
    _google(monkeypatch, _GoogleFalso(ready=False))
    monkeypatch.setattr("src.ollama_client.llm", _LLMFalso(RuntimeError("ollama caido")))

    res = await auto.detect_commitments(force=True)

    assert res["success"] is False
    assert "LLM" in res["error"]
    assert "commitments:last" not in store["kv"]
    assert store["tareas"] == []


async def test_omite_confianza_baja(monkeypatch):
    store = _db_falso(monkeypatch, chats=[_chat_reciente("algo vago")])
    _google(monkeypatch, _GoogleFalso(ready=False))
    llm = _LLMFalso(_deteccion("Tarea dudosa", None, confianza="baja"))
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments(force=True)

    assert res["success"] is True
    assert res["creadas"] == []
    assert res["omitidas_baja_confianza"] == 1
    assert store["tareas"] == []


async def test_recordatorio_crea_alerta_de_seguimiento(monkeypatch):
    store = _db_falso(monkeypatch, chats=[_chat_reciente("cliente: te lo envio el 2026-10-08")])
    _google(monkeypatch, _GoogleFalso(ready=False))
    _bot_falso(monkeypatch)
    llm = _LLMFalso(
        _deteccion("Seguir al cliente del presupuesto", "2026-10-08", tipo="recordatorio")
    )
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments(force=True)

    assert res["creadas"][0]["tipo"] == "recordatorio"
    assert store["alertas"][0][0].startswith("Seguimiento:")
    assert store["alertas"][0][1] == "2026-10-08"
    assert store["tareas"] == []


async def test_sin_fuentes_devuelve_honesto(monkeypatch):
    store = _db_falso(monkeypatch, chats=[])
    _google(monkeypatch, _GoogleFalso(ready=False))
    llm = _LLMFalso("{}")
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments(force=True)

    assert res["success"] is True
    assert res["creadas"] == []
    assert "sin mensajes" in res["nota"]
    assert llm.llamadas == 0
    assert store["kv"]["commitments:last"]


async def test_con_google_usa_tasks_en_google(monkeypatch):
    store = _db_falso(monkeypatch, chats=[_chat_reciente("renovar certificado el 2026-11-01")])
    google = _GoogleFalso(ready=True)
    _google(monkeypatch, google)
    _bot_falso(monkeypatch)
    monkeypatch.setattr(
        "src.ollama_client.llm", _LLMFalso(_deteccion("Renovar certificado", "2026-11-01"))
    )

    res = await auto.detect_commitments(force=True)

    assert google.creadas == [("Renovar certificado", "2026-11-01")]
    assert store["tareas"] == []
    assert res["creadas"][0]["fecha"] == "2026-11-01"


async def test_analiza_correo_reciente(monkeypatch):
    store = _db_falso(monkeypatch, chats=[])
    google = _GoogleFalso(
        ready=True,
        mails=[
            {
                "id": "m1",
                "subject": "Presupuesto",
                "from": "ana@cliente.com",
                "date": "Sun, 04 Oct 2026",
                "snippet": "Te envio el presupuesto el viernes",
            }
        ],
    )
    _google(monkeypatch, google)
    _bot_falso(monkeypatch)
    llm = _LLMFalso(_deteccion("Esperar presupuesto de Ana", "2026-10-09", fuente="correo"))
    monkeypatch.setattr("src.ollama_client.llm", llm)

    res = await auto.detect_commitments(force=True)

    assert res["fuentes"]["correos"] == 1
    assert "[correo]" in llm.contenidos[0]
    assert res["creadas"][0]["fuente"] == "correo"
    assert store["tareas"] == []
