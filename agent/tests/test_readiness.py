"""Readiness endpoint and dependency state tests (tasks 0.6 and 3.3)."""

import asyncio
import os

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


async def _down_async():
    return {"status": "unhealthy", "detail": "dependency down"}


def _down_sync():
    return {"status": "unhealthy", "detail": "dependency down"}


async def _degraded_async():
    return {"status": "degraded", "detail": "model will load on first request"}


class TestReadyEndpoint:
    def _patch(
        self, monkeypatch, ai=_ok_async, vector_db=_ok_async, vault=_ok_sync, telegram=_ok_sync
    ):
        monkeypatch.setattr(webhook_server, "_check_ai", ai)
        monkeypatch.setattr(webhook_server, "_check_vector_db", vector_db)
        monkeypatch.setattr(webhook_server, "_check_vault", vault)
        monkeypatch.setattr(webhook_server, "_check_telegram", telegram)

    def test_ready_when_all_checks_ok(self, client, monkeypatch):
        self._patch(monkeypatch)
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["status"] == "ready"
        assert set(body["checks"]) == {"ai", "vector_db", "vault", "telegram"}

    def test_not_ready_when_ai_down(self, client, monkeypatch):
        self._patch(monkeypatch, ai=_down_async)
        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        assert body["status"] == "not_ready"
        assert body["checks"]["ai"]["status"] == "unhealthy"
        assert body["checks"]["vector_db"]["status"] == "ok"

    def test_not_ready_when_vector_db_down(self, client, monkeypatch):
        self._patch(monkeypatch, vector_db=_down_async)
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["vector_db"]["status"] == "unhealthy"

    def test_not_ready_when_vault_down(self, client, monkeypatch):
        self._patch(monkeypatch, vault=_down_sync)
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["vault"]["status"] == "unhealthy"

    def test_not_ready_when_telegram_down(self, client, monkeypatch):
        self._patch(monkeypatch, telegram=_down_sync)
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["telegram"]["status"] == "unhealthy"

    def test_degraded_ai_keeps_service_ready(self, client, monkeypatch):
        """A not-yet-loaded model is a caveat, not a failure (task 3.3)."""
        self._patch(monkeypatch, ai=_degraded_async)
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["ready"] is True

    def test_degraded_vault_read_only_keeps_service_ready(self, client, monkeypatch):
        self._patch(
            monkeypatch,
            vault=lambda: {"status": "degraded", "detail": "vault is read-only"},
        )
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    def test_health_reports_version(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["version"] == "0.2.0"

    def test_liveness_stays_ok_when_dependencies_down(self, client, monkeypatch):
        self._patch(
            monkeypatch,
            ai=_down_async,
            vector_db=_down_async,
            vault=_down_sync,
            telegram=_down_sync,
        )
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200


class TestAiCheck:
    async def test_uses_provider_check_health(self, monkeypatch):
        import src.ollama_client as ollama_module

        class _FakeProvider:
            async def check_health(self):
                return {"status": "degraded", "provider": "fake"}

        monkeypatch.setattr(ollama_module, "llm", _FakeProvider())
        check = await webhook_server._check_ai()
        assert check == {"status": "degraded", "provider": "fake"}

    async def test_provider_exception_becomes_unhealthy(self, monkeypatch):
        import src.ollama_client as ollama_module

        class _BrokenProvider:
            async def check_health(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(ollama_module, "llm", _BrokenProvider())
        check = await webhook_server._check_ai()
        assert check["status"] == "unhealthy"
        assert "boom" in check["detail"]


class TestVaultCheck:
    def test_missing_vault_is_unhealthy(self, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "obsidian_vault_dir", "/tmp/does-not-exist-rafita")
        assert webhook_server._check_vault()["status"] == "unhealthy"

    def test_file_instead_of_dir_is_unhealthy(self, monkeypatch, tmp_path):
        from src.config import settings

        target = tmp_path / "vault-file"
        target.write_text("x", encoding="utf-8")
        monkeypatch.setattr(settings, "obsidian_vault_dir", str(target))
        assert webhook_server._check_vault()["status"] == "unhealthy"

    def test_writable_dir_is_ok(self, monkeypatch, tmp_path):
        from src.config import settings

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(settings, "obsidian_vault_dir", str(vault))
        check = webhook_server._check_vault()
        assert check["status"] == "ok"
        assert check["writable"] is True

    def test_read_only_vault_is_degraded(self, monkeypatch, tmp_path):
        from src.config import settings

        vault = tmp_path / "vault-ro"
        vault.mkdir()
        monkeypatch.setattr(settings, "obsidian_vault_dir", str(vault))
        real_access = os.access
        monkeypatch.setattr(
            os,
            "access",
            lambda path, mode: False if mode == os.W_OK else real_access(path, mode),
        )
        assert webhook_server._check_vault()["status"] == "degraded"


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
