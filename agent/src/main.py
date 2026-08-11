import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

from src.bot import bot
from src.config import settings
from src.database import db
from src.logger import logger
from src.ollama_client import llm
from src.utils.google_calendar_manager import gcal
from src.utils.obsidian_manager import initialize_vault_structure as init_obsidian_vault
from src.utils.telemetry import metrics
from src.utils.vector_manager import vector_db

TEMP_CLEANUP_DIRS = [
    Path("/tmp/rafita_uploads"),
    Path("/app/tts_models"),
]
TEMP_FILE_EXTENSIONS = (".ogg", ".wav", ".mp3", ".jpg", ".jpeg", ".png", ".webp")
GC_HOUR = 3
GC_MINUTE = 0


class ProactiveWorker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._gc_run_count: int = 0
        self._consecutive_failures: int = 0

    async def start(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self._task = asyncio.create_task(self._run_loop())
        logger.info("ProactiveWorker started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("ProactiveWorker stopped")

    async def _run_loop(self) -> None:
        try:
            while True:
                now = datetime.now()
                check_hour, check_minute = map(
                    int, settings.proactive_check_time.split(":")
                )
                target = now.replace(
                    hour=check_hour, minute=check_minute, second=0, microsecond=0
                )
                if now >= target:
                    target = target.replace(day=target.day + 1)
                wait_seconds = (target - now).total_seconds()
                logger.info(
                    "Next proactive check at %s (in %d seconds)",
                    target.strftime("%Y-%m-%d %H:%M"),
                    int(wait_seconds),
                )
                try:
                    await asyncio.wait_for(
                        self._sleep_until(target),
                        timeout=wait_seconds + 10,
                    )
                except TimeoutError:
                    pass
                if self._shutdown_event and self._shutdown_event.is_set():
                    break
                try:
                    await self._check_and_notify()
                    self._consecutive_failures = 0
                except Exception as e:
                    self._consecutive_failures += 1
                    backoff = min(self._consecutive_failures * 60, 600)
                    logger.exception("Proactive check failed (%d): %s. Backoff %ds", self._consecutive_failures, e, backoff)
                    await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            logger.info("ProactiveWorker loop cancelled")
        except Exception as e:
            logger.exception("ProactiveWorker error: %s", e)

    async def _sleep_until(self, target: datetime) -> None:
        while True:
            now = datetime.now()
            if now >= target:
                return
            remaining = (target - now).total_seconds()
            if remaining <= 0:
                return
            wait = min(remaining, 60)
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                raise

    async def _check_and_notify(self) -> None:
        logger.info("Running proactive check...")
        try:
            now = datetime.now()
            if now.hour == GC_HOUR and now.minute >= GC_MINUTE and now.minute < GC_MINUTE + 10:
                await self._run_garbage_collection()
            chat_ids = await db.get_all_chat_ids()
            for chat_id in chat_ids:
                await self._notify_expiring_events(chat_id)
                await self._notify_unread_alerts(chat_id)
                await self._notify_expiring_alerts(chat_id)
                await self._notify_recurring_alerts(chat_id)
        except Exception as e:
            logger.exception("Proactive check failed: %s", e)

    async def _run_garbage_collection(self) -> None:
        logger.info("Running garbage collection (cleanup temp files)...")
        cutoff = datetime.now().timestamp() - 86400
        deleted = 0
        for clean_dir in TEMP_CLEANUP_DIRS:
            if not clean_dir.exists():
                continue
            for item in clean_dir.iterdir():
                if item.is_file() and item.suffix.lower() in TEMP_FILE_EXTENSIONS:
                    try:
                        if item.stat().st_mtime < cutoff:
                            item.unlink()
                            deleted += 1
                    except Exception as e:
                        logger.debug("GC: could not delete %s: %s", item.name, e)
        self._gc_run_count += 1
        if self._gc_run_count % 7 == 0:
            try:
                await db.execute("VACUUM")
                logger.info("Database VACUUM completed")
            except Exception as e:
                logger.warning("Database VACUUM failed: %s", e)
        logger.info("Garbage collection done: %d files removed (run #%d)", deleted, self._gc_run_count)

    async def _notify_recurring_alerts(self, chat_id: int) -> None:
        due = await db.get_due_recurring_alerts()
        for alert in due:
            if alert["chat_id"] != chat_id:
                continue
            pattern = alert.get("pattern", "")
            message = alert["message"]
            await bot.send_proactive_message(
                chat_id,
                "🔔 *Recordatorio recurrente:* %s" % message,
            )
            next_run = await db.compute_next_run(pattern, alert["next_run"])
            if next_run:
                await db.update_alert_next_run(alert["id"], next_run)
            else:
                await db.mark_alert_read(alert["id"])

    async def _notify_expiring_events(self, chat_id: int) -> None:
        for days in [30, 15, 7, 1]:
            events = await db.get_expiring_events(days)
            events_for_chat = [e for e in events if e["chat_id"] == chat_id]
            if not events_for_chat:
                continue
            label = "hoy" if days == 1 else f"en {days} días"
            lines = [f"📅 *Recordatorio: Eventos {label}*"]
            for ev in events_for_chat:
                lines.append(
                    f"  • {ev['title']} - {ev['event_datetime']}"
                )
            await bot.send_proactive_message(chat_id, "\n".join(lines))

    async def _notify_unread_alerts(self, chat_id: int) -> None:
        count = await db.get_unread_alert_count(chat_id)
        if count == 0:
            return
        text = (
            f"🔔 *Tienes {count} alerta{'s' if count != 1 else ''} pendiente{'s' if count != 1 else ''}*"
            f"\nUsa /alertas para revisarlas."
        )
        await bot.send_proactive_message(chat_id, text)

    async def _notify_expiring_alerts(self, chat_id: int) -> None:
        for days in [7, 1]:
            alerts = await db.get_expiring_alerts(days)
            alerts_for_chat = [a for a in alerts if a["chat_id"] == chat_id]
            if not alerts_for_chat:
                continue
            label = "hoy" if days == 1 else f"en {days} días"
            lines = [f"⚠️ *Alertas por expirar {label}*"]
            for al in alerts_for_chat:
                lines.append(
                    f"  • {al['message']} (expira: {al['expires_at']})"
                )
            await bot.send_proactive_message(chat_id, "\n".join(lines))


class BackgroundIndexer:
    """Alias del VaultIndexer para compatibilidad hacia atras en Application."""

    def __init__(self):
        from src.utils.vault_indexer import VaultIndexer
        self._indexer = VaultIndexer()

    async def start(self, shutdown_event: asyncio.Event) -> None:
        await self._indexer.start(shutdown_event)

    async def stop(self) -> None:
        await self._indexer.stop()

    async def index_all(self):
        return await self._indexer.index_all()


class Application:
    def __init__(self):
        self._shutdown_event: asyncio.Event | None = None
        self._proactive_worker = ProactiveWorker()
        self._indexer = BackgroundIndexer()
        self._gateway_task: asyncio.Task | None = None
        self._voice_stream_task: asyncio.Task | None = None

    async def _ensure_embedding_model(self) -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as hc:
                resp = await hc.get("%s/api/tags" % settings.ollama_host.rstrip("/"))
                resp.raise_for_status()
                data = resp.json()
                models = data.get("models", [])
                model_ids = [m["name"] for m in models]
                if settings.embedding_model not in model_ids:
                    logger.info(
                        "Pulling embedding model '%s' (this may take a while)...",
                        settings.embedding_model,
                    )
                    pull_resp = await hc.post(
                        "%s/api/pull" % settings.ollama_host.rstrip("/"),
                        json={"name": settings.embedding_model},
                        timeout=300.0,
                    )
                    pull_resp.raise_for_status()
                    logger.info("Embedding model '%s' pulled successfully.", settings.embedding_model)
                if settings.ollama_model not in model_ids:
                    logger.info(
                        "Pulling main model '%s' (this may take a while)...",
                        settings.ollama_model,
                    )
                    pull_resp = await hc.post(
                        "%s/api/pull" % settings.ollama_host.rstrip("/"),
                        json={"name": settings.ollama_model},
                        timeout=1800.0,
                    )
                    pull_resp.raise_for_status()
                    logger.info("Main model '%s' pulled successfully.", settings.ollama_model)
                if settings.ollama_vision_model not in model_ids:
                    logger.info(
                        "Pulling vision model '%s'...",
                        settings.ollama_vision_model,
                    )
                    pull_resp = await hc.post(
                        "%s/api/pull" % settings.ollama_host.rstrip("/"),
                        json={"name": settings.ollama_vision_model},
                        timeout=1800.0,
                    )
                    pull_resp.raise_for_status()
                    logger.info("Vision model '%s' pulled successfully.", settings.ollama_vision_model)
        except Exception:
            logger.warning("Could not verify/pull models.")

    async def startup(self) -> None:
        logger.info("=" * 50)
        logger.info("Rafita Agent Core - Starting up")
        logger.info("=" * 50)

        from src.utils.hardware_detect import detect_and_log
        detect_and_log()

        logger.info("Step 0/9: Initializing Obsidian vault structure...")
        await init_obsidian_vault()

        logger.info("Step 1/9: Initializing database...")
        await db.initialize()

        logger.info("Step 2/9: Connecting to Ollama...")
        await llm.initialize()

        health = await llm.check_health()
        logger.info(
            "Ollama health: %s (latency: %dms)",
            health["status"], health.get("latency_ms", 0),
        )

        await self._ensure_embedding_model()

        logger.info("Step 3/9: Initializing Vector DB (RAG)...")
        try:
            await vector_db.initialize()
            stats = await vector_db.get_stats()
            logger.info(
                "Vector DB ready: %d chunks from %d documents",
                stats["total_chunks"], stats["total_documents"],
            )
            if stats["total_chunks"] == 0:
                logger.info("Vector DB empty - running initial vault backfill...")
                backfill_result = await self._indexer.index_all()
                logger.info(
                    "Backfill: %s",
                    backfill_result.get("message", "complete"),
                )
        except Exception as e:
            logger.warning("Vector DB init skipped: %s", e)

        logger.info("Step 4/9: Initializing Google Calendar...")
        gcal_ok = await gcal.initialize()
        if gcal_ok:
            sync_result = await gcal.sync_from_local_db(db)
            logger.info("Google Calendar sync: %s", sync_result.get("message", "ok"))

        logger.info("Step 5/9: Initializing App Connectors...")
        try:
            from src.utils.app_connector import connector
            await connector.initialize()
            conns = connector.list_connectors()
            logger.info("App connectors loaded: %d active", len(conns))
        except Exception as e:
            logger.warning("App connector init skipped: %s", e)

        logger.info("Step 5.5/9: Initializing Google Service...")
        try:
            from src.services.google_service import google_service
            g_ok = await google_service.initialize()
            if g_ok:
                logger.info("Google Service: autenticado y listo")
            else:
                logger.info("Google Service: no autenticado (usar generate_google_auth_link)")
        except Exception as e:
            logger.warning("Google Service init skipped: %s", e)

        logger.info("Step 6/9: Starting FastAPI Gateway (port 8000)...")
        try:
            import os as _os

            from src.utils.webhook_server import configure_gateway, start_gateway_server
            webhook_secret = _os.environ.get("WEBHOOK_SECRET", "rafita-secure-2026")
            configure_gateway(webhook_secret, bot_ref=bot)
            self._gateway_task = asyncio.create_task(start_gateway_server(port=8000))
            logger.info("Gateway started on port 8000")
        except Exception as e:
            logger.warning("Gateway start skipped: %s", e)

        logger.info("Step 7/9: Starting Voice Stream Server (port 8001)...")
        try:
            from src.voice_stream.server import start_voice_stream_server
            self._voice_stream_task = asyncio.create_task(start_voice_stream_server(port=8001))
            logger.info("Voice Stream started on port 8001")
        except Exception as e:
            logger.warning("Voice Stream start skipped: %s", e)

        logger.info("Step 8/9: Initializing Telegram bot...")
        await bot.initialize()

        logger.info("Step 9/9: Starting background workers...")
        await self._proactive_worker.start(self._shutdown_event)
        await self._indexer.start(self._shutdown_event)

        asyncio.create_task(_catch_up_scan())
        asyncio.create_task(_health_monitor())

        logger.info("Startup complete. All services running.")
        logger.info("=" * 50)

    async def shutdown(self) -> None:
        logger.info("Shutting down Rafita Agent Core...")
        if self._gateway_task:
            self._gateway_task.cancel()
            try:
                await self._gateway_task
            except asyncio.CancelledError:
                pass
            logger.info("Gateway stopped")
        if self._voice_stream_task:
            self._voice_stream_task.cancel()
            try:
                await self._voice_stream_task
            except asyncio.CancelledError:
                pass
            logger.info("Voice Stream stopped")
        try:
            await self._indexer.stop()
        except Exception as e:
            logger.error("Error stopping indexer: %s", e)
        try:
            await self._proactive_worker.stop()
        except Exception as e:
            logger.error("Error stopping proactive worker: %s", e)
        try:
            from src.utils.app_connector import connector
            await connector.close()
        except Exception:
            pass
        try:
            await bot.stop()
        except Exception as e:
            logger.error("Error stopping bot: %s", e)
        try:
            await bot.destroy()
        except Exception as e:
            logger.error("Error destroying bot: %s", e)
        try:
            await llm.close()
        except Exception as e:
            logger.error("Error closing Ollama client: %s", e)
        try:
            await vector_db.close()
        except Exception as e:
            logger.error("Error closing vector DB: %s", e)
        try:
            await gcal.close()
        except Exception as e:
            logger.error("Error closing Google Calendar: %s", e)
        try:
            await db.close()
        except Exception as e:
            logger.error("Error closing database: %s", e)
        logger.info("Shutdown complete.")

    async def run(self) -> None:
        self._shutdown_event = asyncio.Event()

        def _signal_handler():
            logger.info("Received shutdown signal")
            self._shutdown_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await self.startup()

        reconnect_attempt = 0
        while not self._shutdown_event.is_set():
            try:
                await bot.start()
                logger.info("Rafita Agent is running. Press Ctrl+C to stop.")
                reconnect_attempt = 0
                await self._shutdown_event.wait()
            except asyncio.CancelledError:
                break
            except Exception as e:
                reconnect_attempt += 1
                backoff = min(5 * reconnect_attempt, 60)
                logger.exception(
                    "Bot polling error (attempt %d). Reconnecting in %ds: %s",
                    reconnect_attempt, backoff, e,
                )
                try:
                    await bot.stop()
                except Exception:
                    pass
                if self._shutdown_event.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        self._wait_with_shutdown(backoff),
                        timeout=backoff + 5,
                    )
                except TimeoutError:
                    continue
        await self.shutdown()

    async def _wait_with_shutdown(self, seconds: float) -> None:
        assert self._shutdown_event is not None
        for _ in range(int(seconds)):
            if self._shutdown_event.is_set():
                return
                await asyncio.sleep(1)


async def _health_monitor() -> None:
    """Periodic health check with admin alert capability."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            from src.utils.vector_manager import vector_db
            vstats = await vector_db.get_stats()
            msnap = metrics.snapshot()
            fail_count = msnap.get("counters", {}).get("tool_calls_failed", 0)
            if fail_count > 10:
                logger.warning(
                    "ALERT: %d tool failures detected. Last snapshot: %s",
                    fail_count,
                    dict(msnap.get("histograms", {}).items()),
                )
            if vstats["total_chunks"] == 0:
                logger.warning(
                    "ALERT: Vector DB is empty! Backfill may have failed or vault is empty."
                )
        except Exception as e:
            logger.debug("Health monitor check skipped: %s", e)


async def _catch_up_scan() -> None:
    try:
        from src.database import db
        from src.utils.message_scanner import scan_messages
        chat_ids = await db.get_all_chat_ids()
        for cid in chat_ids:
            result = await scan_messages(cid, limit=30)
            if result.get("messages_scanned", 0) > 0:
                logger.info(
                    "Catch-up scan: chat %d -> %d messages processed",
                    cid, result["messages_scanned"],
                )
    except Exception as e:
        logger.debug("Catch-up scan skipped: %s", e)


def main() -> None:
    app = Application()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
