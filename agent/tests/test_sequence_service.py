"""Tests de las secuencias de email (Fase 2)."""

from datetime import date, timedelta

import src.services.sequence_service as seq
from src.config import settings


class _FakeDB:
    def __init__(self):
        self.sequences: dict[int, dict] = {}
        self.steps: dict[int, dict] = {}
        self._seq = 0
        self._step = 0

    async def create_sequence(self, name, contact_name, contact_email, context=""):
        self._seq += 1
        self.sequences[self._seq] = {
            "id": self._seq,
            "name": name,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "context": context,
            "status": "active",
            "created_at": date.today().isoformat() + " 00:00:00",
        }
        return self._seq

    async def add_sequence_step(self, sequence_id, step_no, delay_days, goal):
        self._step += 1
        self.steps[self._step] = {
            "id": self._step,
            "sequence_id": sequence_id,
            "step_no": step_no,
            "delay_days": delay_days,
            "goal": goal,
            "status": "pending",
            "subject": None,
            "body": None,
            "sent_at": None,
        }
        return self._step

    async def list_sequences(self, status=None):
        vals = list(self.sequences.values())
        return [s for s in vals if s["status"] == status] if status else vals

    async def get_sequence(self, sequence_id):
        return self.sequences.get(int(sequence_id))

    async def list_sequence_steps(self, sequence_id):
        return [s for s in self.steps.values() if s["sequence_id"] == int(sequence_id)]

    async def update_sequence_status(self, sequence_id, status):
        self.sequences[int(sequence_id)]["status"] = status

    async def update_sequence_step(self, step_id, status, subject=None, body=None, message_id=None):
        step = self.steps[int(step_id)]
        step["status"] = status
        if subject:
            step["subject"] = subject
        if body:
            step["body"] = body
        if status == "sent":
            step["sent_at"] = "2026-09-29 09:30:00"

    async def due_sequence_steps(self):
        hoy = date.today()
        out = []
        for step in self.steps.values():
            seq_data = self.sequences.get(step["sequence_id"])
            if not seq_data or seq_data["status"] != "active" or step["status"] != "pending":
                continue
            inicio = date.fromisoformat(seq_data["created_at"][:10])
            if inicio + timedelta(days=step["delay_days"]) <= hoy:
                out.append(
                    {
                        **step,
                        "step_id": step["id"],
                        "sequence_id": seq_data["id"],
                        "sequence_name": seq_data["name"],
                        "contact_name": seq_data["contact_name"],
                        "contact_email": seq_data["contact_email"],
                        "context": seq_data["context"],
                        "sequence_created": seq_data["created_at"],
                    }
                )
        return out


class _FakeLLM:
    async def chat(self, messages, max_tokens=None, **kwargs):
        return "Asunto: Propuesta para ti\n\nHola, te escribo para retomar el tema.\n\nUn saludo."


class _FakeGoogle:
    is_ready = True

    def __init__(self):
        self.enviados = []
        self.respuestas = []

    async def send_email(self, to, subject, body):
        self.enviados.append((to, subject, body))
        return {"success": True, "id": "m1"}

    async def search_gmail(self, query, max_results=5):
        return {"success": True, "messages": list(self.respuestas)}


def _preparar(monkeypatch):
    fake_db = _FakeDB()
    fake_google = _FakeGoogle()
    notas = []

    async def nota(nombre, texto):
        notas.append((nombre, texto))

    monkeypatch.setattr(seq, "db", fake_db)
    monkeypatch.setattr(seq, "_nota_crm", nota)
    monkeypatch.setattr("src.ollama_client.llm", _FakeLLM())
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_google)
    return fake_db, fake_google, notas


# ---------- parseo de pasos ----------


def test_parse_pasos_formatos():
    assert seq._parse_pasos(None) == seq.PASOS_POR_DEFECTO
    assert seq._parse_pasos("0:presentacion,3:seguimiento") == [
        (0, "presentacion"),
        (3, "seguimiento"),
    ]
    assert seq._parse_pasos([{"delay_days": 1, "goal": "algo"}]) == [(1, "algo")]
    assert seq._parse_pasos("basura") == seq.PASOS_POR_DEFECTO


# ---------- crear ----------


async def test_crear_secuencia(monkeypatch):
    fake_db, _google, _notas = _preparar(monkeypatch)
    resultado = await seq.crear_secuencia(
        "Propuesta web", "Maria", "maria@x.com", "web con reservas", "0:presentacion,3:seguimiento"
    )
    assert resultado["success"] is True
    assert len(fake_db.sequences) == 1
    assert len(fake_db.steps) == 2
    assert "dia 0" in resultado["message"]


async def test_crear_secuencia_valida_email(monkeypatch):
    _preparar(monkeypatch)
    malo = await seq.crear_secuencia("X", "Maria", "sin-arroba")
    assert malo["success"] is False


# ---------- ejecutar ----------


async def test_ejecutar_dry_run_no_envia(monkeypatch):
    fake_db, fake_google, _notas = _preparar(monkeypatch)
    await seq.crear_secuencia("S", "Maria", "maria@x.com", pasos="0:presentacion")
    resultado = await seq.ejecutar_secuencias(dry_run=True)
    assert resultado["changes"] == 1
    assert fake_google.enviados == []
    assert resultado["sent"][0]["dry_run"] is True
    paso = list(fake_db.steps.values())[0]
    assert paso["status"] == "pending"
    assert "Propuesta" in (paso["subject"] or "")


async def test_ejecutar_envia_y_anota(monkeypatch):
    fake_db, fake_google, notas = _preparar(monkeypatch)
    await seq.crear_secuencia("S", "Maria", "maria@x.com", pasos="0:presentacion")
    resultado = await seq.ejecutar_secuencias()
    assert resultado["changes"] == 1
    assert fake_google.enviados and fake_google.enviados[0][0] == "maria@x.com"
    paso = list(fake_db.steps.values())[0]
    assert paso["status"] == "sent" and paso["sent_at"]
    assert any("enviado" in texto for _nombre, texto in notas)


async def test_ejecutar_para_si_respondio(monkeypatch):
    fake_db, fake_google, notas = _preparar(monkeypatch)
    fake_google.respuestas = [{"subject": "Re: propuesta", "from": "maria@x.com"}]
    await seq.crear_secuencia("S", "Maria", "maria@x.com", pasos="0:presentacion")
    resultado = await seq.ejecutar_secuencias()
    assert resultado["stopped"] == ["S"]
    assert fake_db.sequences[1]["status"] == "stopped"
    assert fake_google.enviados == []
    assert any("respondio" in texto for _nombre, texto in notas)


async def test_ejecutar_sin_cambios(monkeypatch):
    _preparar(monkeypatch)
    resultado = await seq.ejecutar_secuencias()
    assert resultado["changes"] == 0
    assert resultado["message"] == ""


async def test_ejecutar_no_envia_si_google_no_conectado(monkeypatch):
    fake_db, fake_google, _notas = _preparar(monkeypatch)
    fake_google.is_ready = False
    await seq.crear_secuencia("S", "Maria", "maria@x.com", pasos="0:presentacion")
    resultado = await seq.ejecutar_secuencias()
    assert fake_google.enviados == []
    assert resultado["changes"] == 0


# ---------- handle ----------


async def test_handle_acciones(monkeypatch):
    fake_db, _google, _notas = _preparar(monkeypatch)
    vacio = await seq.handle("list", {})
    assert "No hay secuencias" in vacio["message"]
    creada = await seq.handle("create", {"nombre": "S", "contacto": "Maria", "email": "m@x.com"})
    assert creada["success"]
    listado = await seq.handle("list", {})
    assert "Maria" in listado["message"]
    parada = await seq.handle("stop", {"sequence_id": 1})
    assert parada["success"] and fake_db.sequences[1]["status"] == "stopped"
    reanudada = await seq.handle("resume", {"sequence_id": 1})
    assert reanudada["success"] and fake_db.sequences[1]["status"] == "active"
    malo = await seq.handle("loquesea", {})
    assert malo["success"] is False


def test_endpoint_requiere_firma():
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    webhook_server.configure_gateway("secreto-seq")
    client = TestClient(webhook_server.app)
    resp = client.post("/automation/sequences-run", json={})
    assert resp.status_code in (401, 503)


def test_formato_iso_local():
    assert len(seq._hoy_iso()) == 10
    assert settings.timezone  # contexto: el servicio usa la zona configurada
