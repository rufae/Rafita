"""Readiness endpoint and dependency state tests (task 0.6)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.bot import RafitaBot
from src.utils import webhook_server


@pytest.fixture
def client():
    return TestClient(webhook_server.app)


async def _ok_async():
    return {"status": "ok"}


def _ok_sync():
    return {"status": "ok"}


async def _fail_async():
    return {"status": "error", "detail": "dependency down"}


def _fail_sync():
    return {"status": "error", "detail": "dependency down"}


class TestReadyEndpoint:
    def _patch(self, monkeypatch, ollama=_ok_async, vector_db=_ok_async, telegram=_ok_sync):
        monkeypatch.setattr(webhook_server, "_check_ollama", ollama)
        monkeypatch.setattr(webhook_server, "_check_vector_db", vector_db)
        monkeypatch.setattr(webhook_server, "_check_telegram", telegram)

    def test_ready_when_all_checks_ok(self, client, monkeypatch):
        self._patch(monkeypatch)
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["status"] == "ready"

    def test_not_ready_when_chat_backend_down(self, client, monkeypatch):
        self._patch(monkeypatch, ollama=_fail_async)
        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        assert body["checks"]["ollama"]["status"] == "error"
        assert body["checks"]["vector_db"]["status"] == "ok"

    def test_not_ready_when_vector_db_down(self, client, monkeypatch):
        self._patch(monkeypatch, vector_db=_fail_async)
        body = client.get("/ready").json()
        assert body["ready"] is False
        assert body["checks"]["vector_db"]["status"] == "error"

    def test_not_ready_when_telegram_down(self, client, monkeypatch):
        self._patch(monkeypatch, telegram=_fail_sync)
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["telegram"]["status"] == "error"

    def test_liveness_stays_ok_when_dependencies_down(self, client, monkeypatch):
        self._patch(monkeypatch, ollama=_fail_async, vector_db=_fail_async, telegram=_fail_sync)
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200


class TestPollingStatus:
    def test_not_initialized(self):
        assert RafitaBot().polling_status()["status"] == "error"

    async def test_running(self):
        bot = RafitaBot()
        bot._app = object()  # type: ignore[assignment]
        bot._app_started.set()
        bot._polling_task = asyncio.create_task(asyncio.sleep(30))
        try:
            assert bot.polling_status()["status"] == "ok"
        finally:
            bot._polling_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bot._polling_task


class TestVectorHealth:
    async def test_not_initialized(self):
        from src.utils.vector_manager import VectorManager

        assert (await VectorManager().health())["status"] == "error"

    async def test_initialized_empty_vault_is_ok(self, tmp_path, monkeypatch):
        from src.config import settings
        from src.utils.vector_manager import VectorManager

        monkeypatch.setattr(settings, "vector_db_dir", str(tmp_path / "vdb"))
        manager = VectorManager()
        await manager.initialize()
        health = await manager.health()
        assert health["status"] == "ok"
        assert health["chunks"] == 0
        await manager.close()
