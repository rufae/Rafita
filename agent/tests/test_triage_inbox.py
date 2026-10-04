"""2.b.1 — Triaje de correo → borradores (triage_inbox).

Clasifica la bandeja no leída (urgente/facturas/clientes/otro) con reglas
deterministas y deja borradores profesionales en Gmail sin enviar.
"""

from src.handlers import chat as chat_mod
from src.handlers.chat_tools import TOOLS_DEFINITIONS


def _definicion():
    for tool in TOOLS_DEFINITIONS:
        if tool["function"]["name"] == "triage_inbox":
            return tool["function"]
    raise AssertionError("triage_inbox no esta en TOOLS_DEFINITIONS")


def test_definicion():
    fn = _definicion()
    desc = fn["description"]
    assert "triaja" in desc or "clasifica" in desc
    assert "urgente" in desc
    assert "facturas" in desc
    assert "SIN enviar" in desc
    assert "Gmail conectado" in desc


class _FakeG:
    def __init__(self, ready=True, messages=None, draft_ok=True):
        self.is_ready = ready
        self._messages = messages or []
        self.drafts = []
        self.draft_ok = draft_ok
        self.queries = []

    async def search_gmail(self, query, max_results=5):
        self.queries.append(query)
        return {"messages": self._messages}

    async def create_draft(self, to, subject, body):
        if not self.draft_ok:
            return {"success": False, "message": "permiso denegado"}
        self.drafts.append({"to": to, "subject": subject, "body": body})
        return {"success": True, "id": "d%d" % len(self.drafts)}


def _mensajes():
    return [
        {
            "subject": "URGENTE: incidencia en el servidor",
            "from": "soporte <soporte@empresa.com>",
            "date": "2026-10-04",
            "snippet": "se ha caido todo",
        },
        {
            "subject": "Factura 2026-09 del alquiler",
            "from": "Administracion <adm@pisos.es>",
            "date": "2026-10-03",
            "snippet": "adjunto factura",
        },
        {
            "subject": "Reunión el jueves",
            "from": "Ana <ana@clientes.com>",
            "date": "2026-10-02",
            "snippet": "te parece bien?",
        },
        {
            "subject": "Newsletter semanal",
            "from": "news@revista.com>",
            "date": "2026-10-01",
            "snippet": "novedades de la semana",
        },
    ]


async def test_clasifica_y_no_borradores_para_otro(monkeypatch):
    fake = _FakeG(messages=_mensajes())
    monkeypatch.setattr(chat_mod, "google_services", fake)
    monkeypatch.setattr(_fake_crm_mod(), "listar", _crm_ana())

    result = await chat_mod._execute_tool(1, "triage_inbox", {"max_drafts": 0})
    assert result["success"] is True
    cats = [c["categoria"] for c in result["correos"]]
    assert cats == ["urgente", "facturas", "clientes", "otro"]
    assert result["borradores"] == 0
    assert fake.drafts == []
    assert fake.queries == ["newer_than:3d is:unread"]
    assert "• urgente ·" in result["message"]
    assert "4 correo(s)" in result["message"]
    assert "3 urgente" not in result["message"]  # 1 de cada uno


async def test_borradores_profesionales_para_accionables(monkeypatch):
    fake = _FakeG(messages=_mensajes())
    monkeypatch.setattr(chat_mod, "google_services", fake)
    monkeypatch.setattr(_fake_crm_mod(), "listar", _crm_ana())

    result = await chat_mod._execute_tool(1, "triage_inbox", {})
    assert result["success"] is True
    assert result["borradores"] == 3  # urgente + facturas + clientes (no 'otro')
    assert len(fake.drafts) == 3
    assert fake.drafts[0]["to"] == "soporte@empresa.com"
    assert fake.drafts[0]["subject"].startswith("Re: ")
    assert "URGENTE" in fake.drafts[0]["subject"]
    assert "URGENTE: incidencia" in fake.drafts[0]["body"]
    assert "Saludos cordiales" in fake.drafts[0]["body"]
    assert all(d["to"] != "news@revista.com" for d in fake.drafts)
    assert "3 borrador(es) creados" in result["message"]


async def test_limita_borradores(monkeypatch):
    fake = _FakeG(messages=_mensajes())
    monkeypatch.setattr(chat_mod, "google_services", fake)
    monkeypatch.setattr(_fake_crm_mod(), "listar", _crm_ana())

    result = await chat_mod._execute_tool(1, "triage_inbox", {"max_drafts": 1})
    assert result["borradores"] == 1
    assert len(fake.drafts) == 1
    assert len(result["correos"]) == 4


async def test_fallo_de_borrador_se_reporta(monkeypatch):
    fake = _FakeG(messages=_mensajes()[:1], draft_ok=False)
    monkeypatch.setattr(chat_mod, "google_services", fake)
    monkeypatch.setattr(_fake_crm_mod(), "listar", _crm_ana())

    result = await chat_mod._execute_tool(1, "triage_inbox", {})
    assert result["success"] is True
    assert result["borradores"] == 0
    assert len(result["borradores_fallidos"]) == 1
    assert "permiso denegado" in result["message"]


async def test_sin_gmail_es_honesta(monkeypatch):
    monkeypatch.setattr(chat_mod, "google_services", _FakeG(ready=False))
    result = await chat_mod._execute_tool(1, "triage_inbox", {})
    assert result["success"] is False
    assert "Gmail no está conectado" in result["message"]


async def test_bandeja_vacia(monkeypatch):
    monkeypatch.setattr(chat_mod, "google_services", _FakeG(messages=[]))
    result = await chat_mod._execute_tool(1, "triage_inbox", {"days": 7})
    assert result["success"] is True
    assert result["correos"] == []
    assert "últimos 7 días" in result["message"]


def test_email_from():
    assert chat_mod._email_from("Ana <ANA@X.com>") == "ANA@X.com"
    assert chat_mod._email_from("plain@y.org") == "plain@y.org"
    assert chat_mod._email_from("sin-correo") == ""


def _fake_crm_mod():
    import src.services.crm_service as crm_mod

    return crm_mod


def _crm_ana():
    async def _listar(_estado=None):
        return [
            {
                "nombre": "Ana",
                "email": "ana@clientes.com",
                "estado": "propuesta",
            }
        ]

    return _listar
