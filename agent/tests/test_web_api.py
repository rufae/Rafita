"""Tests de la API web (Fase 3): login, chat, boveda y llamada."""

from fastapi.testclient import TestClient

import src.database as database
import src.utils.web_api as web_api
from src.config import settings
from src.utils.web_auth import hash_password


class _FakeDB:
    """Base de datos web en memoria para los tests."""

    def __init__(self):
        self.users: dict[int, dict] = {}
        self._id = 0

    async def create_web_user(self, email, password_hash, is_admin=False):
        self._id += 1
        self.users[self._id] = {
            "id": self._id,
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "is_admin": bool(is_admin),
        }
        return self._id

    async def get_web_user(self, user_id):
        return self.users.get(int(user_id))

    async def get_web_user_by_email(self, email):
        for user in self.users.values():
            if user["email"] == email.strip().lower():
                return user
        return None

    async def count_web_users(self):
        return len(self.users)

    async def list_web_users(self):
        return list(self.users.values())

    async def get_chat_history(self, chat_id, limit=30):
        # Igual que la BD real: mas reciente primero.
        return [
            {
                "id": 2,
                "chat_id": chat_id,
                "role": "assistant",
                "content": "buenas",
                "created_at": "2026-09-29",
            },
            {
                "id": 1,
                "chat_id": chat_id,
                "role": "user",
                "content": "hola",
                "created_at": "2026-09-29",
            },
        ][:limit]


def _cliente(monkeypatch, tmp_path):
    fake = _FakeDB()
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-web-test")
    monkeypatch.setattr(settings, "web_allow_registration", False)
    monkeypatch.setattr(settings, "voice_call_token", "token-llamada")
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    for nombre in (
        "create_web_user",
        "get_web_user",
        "get_web_user_by_email",
        "count_web_users",
        "list_web_users",
        "get_chat_history",
    ):
        monkeypatch.setattr(database.db, nombre, getattr(fake, nombre))
    from src.utils import webhook_server

    return TestClient(webhook_server.app), fake


async def _crear_usuario(fake, email="admin@x.com", clave="clave12345", admin=True):
    await fake.create_web_user(email, hash_password(clave), is_admin=admin)


def test_login_y_me(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))

    malo = client.post("/api/auth/login", json={"email": "admin@x.com", "password": "nope"})
    assert malo.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"})
    assert ok.status_code == 200
    token = ok.json()["token"]
    assert ok.json()["user"]["is_admin"] is True

    sin = client.get("/api/auth/me")
    assert sin.status_code == 401
    con = client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert con.status_code == 200
    assert con.json()["user"]["email"] == "admin@x.com"


def test_registro_deshabilitado_y_habilitado(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    no = client.post("/api/auth/register", json={"email": "n@x.com", "password": "clave12345"})
    assert no.status_code == 403
    monkeypatch.setattr(settings, "web_allow_registration", True)
    si = client.post("/api/auth/register", json={"email": "n@x.com", "password": "clave12345"})
    assert si.status_code == 200
    corta = client.post("/api/auth/register", json={"email": "o@x.com", "password": "123"})
    assert corta.status_code == 400
    repetida = client.post(
        "/api/auth/register", json={"email": "n@x.com", "password": "clave12345"}
    )
    assert repetida.status_code == 409


def test_chat_requiere_token_y_responde(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]

    assert client.post("/api/chat", json={"message": "hola"}).status_code == 401

    async def fake_generate(text, chat_id):
        assert text == "hola"
        assert chat_id == web_api.WEB_CHAT_BASE + 1
        return "buenas!"

    monkeypatch.setattr("src.core.generate_response", fake_generate)
    resp = client.post(
        "/api/chat",
        json={"message": "hola"},
        headers={"Authorization": "Bearer " + token},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "buenas!"

    hist = client.get("/api/chat/history", headers={"Authorization": "Bearer " + token})
    assert hist.status_code == 200
    assert hist.json()["messages"][0]["content"] == "hola"


def test_vault_crud_y_seguridad_de_rutas(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    vacio = client.get("/api/vault/notes", headers=headers)
    assert vacio.status_code == 200 and vacio.json()["notes"] == []

    creada = client.post(
        "/api/vault/note",
        json={"path": "00-Inbox/prueba.md", "content": "# Hola\ncontenido"},
        headers=headers,
    )
    assert creada.status_code == 200
    listado = client.get("/api/vault/notes?query=contenido", headers=headers)
    assert listado.status_code == 200
    assert listado.json()["notes"][0]["path"] == "00-Inbox/prueba.md"

    leida = client.get("/api/vault/note?path=00-Inbox/prueba.md", headers=headers)
    assert leida.status_code == 200 and "contenido" in leida.json()["content"]

    # Seguridad: traversal y extensiones no permitidas
    malo = client.get("/api/vault/note?path=../fuera.md", headers=headers)
    assert malo.status_code == 400
    no_md = client.post(
        "/api/vault/note", json={"path": "x.sh", "content": "echo"}, headers=headers
    )
    assert no_md.status_code == 400

    borrada = client.delete("/api/vault/note?path=00-Inbox/prueba.md", headers=headers)
    assert borrada.status_code == 200
    assert client.get("/api/vault/note?path=00-Inbox/prueba.md", headers=headers).status_code == 404


def test_call_token_y_spa(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    resp = client.get("/api/call/token", headers={"Authorization": "Bearer " + token})
    assert resp.status_code == 200
    assert resp.json()["token"] == "token-llamada"

    # SPA y PWA servidas por el gateway
    assert client.get("/app/").status_code == 200
    assert "Rafita" in client.get("/app/").text
    assert client.get("/app/manifest.webmanifest").status_code == 200
    assert client.get("/app/sw.js").status_code == 200
    assert client.get("/").status_code in (200, 307)


def test_meetings_api(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    assert client.get("/api/meetings").status_code == 401

    llamadas = {}

    async def fake_crear(user_id, titulo, nombre, datos):
        llamadas["crear"] = (user_id, titulo, nombre, len(datos))
        return {"id": 5, "title": titulo or "Reunion", "status": "processing"}

    async def fake_listar(user_id):
        return [{"id": 5, "title": "Reunion", "status": "done", "duration_s": 60.0}]

    async def fake_detalle(meeting_id, user_id):
        if meeting_id != 5:
            return None
        return {"id": 5, "title": "Reunion", "status": "done", "tasks_list": ["t"]}

    async def fake_borrar(meeting_id, user_id):
        return meeting_id == 5

    monkeypatch.setattr("src.services.meeting_service.crear_desde_subida", fake_crear)
    monkeypatch.setattr("src.services.meeting_service.listar", fake_listar)
    monkeypatch.setattr("src.services.meeting_service.detalle", fake_detalle)
    monkeypatch.setattr("src.services.meeting_service.borrar", fake_borrar)

    subida = client.post(
        "/api/meetings",
        files={"file": ("reunion.webm", b"audio-bytes", "audio/webm")},
        data={"title": "Daily"},
        headers=headers,
    )
    assert subida.status_code == 200
    assert subida.json()["id"] == 5
    assert llamadas["crear"][1] == "Daily"
    assert llamadas["crear"][3] == len(b"audio-bytes")

    listado = client.get("/api/meetings", headers=headers)
    assert listado.status_code == 200 and listado.json()["meetings"][0]["id"] == 5

    detalle = client.get("/api/meetings/5", headers=headers)
    assert detalle.status_code == 200 and detalle.json()["tasks_list"] == ["t"]
    assert client.get("/api/meetings/99", headers=headers).status_code == 404

    assert client.delete("/api/meetings/5", headers=headers).status_code == 200
    assert client.delete("/api/meetings/99", headers=headers).status_code == 404

    vacio = client.post(
        "/api/meetings",
        files={"file": ("vacio.webm", b"", "audio/webm")},
        headers=headers,
    )
    assert vacio.status_code == 400
