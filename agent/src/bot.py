import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from telegram import BotCommand as TelegramBotCommand
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import settings
from src.logger import logger


class RateLimiter:
    def __init__(self, max_per_minute: int = 30):
        self._max_per_minute = max_per_minute
        self._buckets: dict[int, list] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        window = 60.0
        bucket = self._buckets[user_id]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= self._max_per_minute:
            return False
        bucket.append(now)
        return True


class RafitaBot:
    def __init__(self):
        self._app: Application | None = None
        self._rate_limiter = RateLimiter()
        self._conversations: dict[int, bool] = {}
        self._app_started = asyncio.Event()
        self._polling_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        builder = ApplicationBuilder()
        builder.token(settings.telegram_token)
        builder.read_timeout(30)
        builder.write_timeout(30)
        builder.connect_timeout(30)
        builder.pool_timeout(30)
        builder.get_updates_read_timeout(30)
        builder.get_updates_write_timeout(30)
        builder.get_updates_connect_timeout(30)
        builder.get_updates_pool_timeout(30)
        builder.concurrent_updates(True)

        self._app = builder.build()

        await self._ensure_logged_in()

        await self._app.initialize()

        try:
            await self._register_commands()
        except Exception as e:
            logger.warning("Could not register commands (will retry on poll): %s", e)
        self._register_handlers()

        logger.info("RafitaBot initialized successfully")

    async def _ensure_logged_in(self) -> None:
        import httpx

        token = settings.telegram_token
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if r.status_code == 200 and r.json().get("ok"):
                return
            logger.info("Bot is logged out. Re-logging in via getUpdates...")
            r = await client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"timeout": 1, "limit": 1},
            )
            if r.status_code == 200 and r.json().get("ok"):
                logger.info("Bot re-logged in successfully")
            else:
                logger.warning("Could not re-login bot: %s", r.text)

    async def _register_commands(self) -> None:
        assert self._app is not None
        bot_commands = [
            TelegramBotCommand("start", "Iniciar el asistente"),
            TelegramBotCommand("ayuda", "Mostrar ayuda y comandos disponibles"),
            TelegramBotCommand("chat", "Chatear con Rafita (IA)"),
            TelegramBotCommand("evento", "Agregar un evento o recordatorio"),
            TelegramBotCommand("eventos", "Listar eventos próximos"),
            TelegramBotCommand("alerta", "Crear una alerta"),
            TelegramBotCommand("alertas", "Listar alertas activas"),
            TelegramBotCommand("gasto", "Registrar un gasto"),
            TelegramBotCommand("ingreso", "Registrar un ingreso"),
            TelegramBotCommand("finanzas", "Resumen financiero del mes"),
            TelegramBotCommand("exportar", "Exportar datos a Excel"),
            TelegramBotCommand("limpiar", "Limpiar historial de conversación"),
            TelegramBotCommand("backup", "Generar respaldo ZIP de datos"),
            TelegramBotCommand("modo_voz", "Activar/desactivar respuestas por voz"),
            TelegramBotCommand("status", "Panel de control completo del sistema"),
            TelegramBotCommand("setup_google", "Configurar Google Calendar"),
        ]
        await self._app.bot.set_my_commands(bot_commands)

    def _register_handlers(self) -> None:
        assert self._app is not None
        from src.handlers.admin import (
            alerta_command,
            alertas_command,
            evento_command,
            eventos_command,
            setup_google_command,
        )
        from src.handlers.audio import voice_handler as audio_voice_handler
        from src.handlers.chat import (
            ayuda_command,
            chat_command,
            handle_message,
            limpiar_command,
            start_command,
        )
        from src.handlers.dashboard import (
            borrar_clave_command,
            cerebro_command,
            clave_command,
            claves_command,
            escanear_command,
            guardar_clave_command,
            recordar_command,
            resumen_command,
            status_command,
        )
        from src.handlers.files import document_handler, photo_handler
        from src.handlers.finance import (
            exportar_command,
            finanzas_command,
            gasto_command,
            ingreso_command,
        )
        from src.handlers.settings import modo_voz_command
        from src.utils.backup import backup_command

        self._app.add_handler(CommandHandler("start", self._wrap(start_command)))
        self._app.add_handler(CommandHandler("ayuda", self._wrap(ayuda_command)))
        self._app.add_handler(CommandHandler("chat", self._wrap(chat_command)))
        self._app.add_handler(CommandHandler("limpiar", self._wrap(limpiar_command)))

        self._app.add_handler(CommandHandler("gasto", self._wrap(gasto_command)))
        self._app.add_handler(CommandHandler("ingreso", self._wrap(ingreso_command)))
        self._app.add_handler(CommandHandler("finanzas", self._wrap(finanzas_command)))
        self._app.add_handler(CommandHandler("exportar", self._wrap(exportar_command)))

        self._app.add_handler(CommandHandler("evento", self._wrap(evento_command)))
        self._app.add_handler(CommandHandler("eventos", self._wrap(eventos_command)))
        self._app.add_handler(CommandHandler("alerta", self._wrap(alerta_command)))
        self._app.add_handler(CommandHandler("alertas", self._wrap(alertas_command)))

        self._app.add_handler(CommandHandler("backup", self._wrap(backup_command)))

        self._app.add_handler(CommandHandler("setup_google", self._wrap(setup_google_command)))

        self._app.add_handler(CommandHandler("modo_voz", self._wrap(modo_voz_command)))

        self._app.add_handler(CommandHandler("status", self._wrap(status_command)))
        self._app.add_handler(CommandHandler("cerebro", self._wrap(cerebro_command)))
        self._app.add_handler(CommandHandler("recordar", self._wrap(recordar_command)))
        self._app.add_handler(CommandHandler("escanear", self._wrap(escanear_command)))
        self._app.add_handler(CommandHandler("resumen", self._wrap(resumen_command)))
        self._app.add_handler(CommandHandler("guardar_clave", self._wrap(guardar_clave_command)))
        self._app.add_handler(CommandHandler("clave", self._wrap(clave_command)))
        self._app.add_handler(CommandHandler("claves", self._wrap(claves_command)))
        self._app.add_handler(CommandHandler("borrar_clave", self._wrap(borrar_clave_command)))

        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._wrap(handle_message))
        )

        self._app.add_handler(MessageHandler(filters.VOICE, self._wrap(audio_voice_handler)))

        self._app.add_handler(MessageHandler(filters.Document.ALL, self._wrap(document_handler)))

        self._app.add_handler(MessageHandler(filters.PHOTO, self._wrap(photo_handler)))

        self._app.add_error_handler(self._error_handler)

    def _wrap(self, handler_fn: Callable) -> Callable:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
            if not update.effective_user:
                return
            user_id = update.effective_user.id
            if not self._rate_limiter.is_allowed(user_id):
                await self._reply(
                    update, "Demasiadas solicitudes. Espera un momento antes de continuar."
                )
                return
            try:
                return await handler_fn(update, context)
            except Exception as e:
                logger.exception("Handler error for user %d: %s", user_id, e)
                await self._reply(update, "Ocurrió un error interno. El equipo ha sido notificado.")

        return wrapper

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:  # type: ignore[override]
        logger.error("Update %s caused error %s", update, context.error)

    async def start(self) -> None:
        if not self._app:
            raise RuntimeError("Bot not initialized")
        await self._app.start()
        try:
            await self._app.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            logger.debug("deleteWebhook: %s", e)
        self._polling_task = asyncio.create_task(self._raw_poll_loop())
        self._app_started.set()
        logger.info("RafitaBot polling started (direct HTTP poll)")

    async def _raw_poll_loop(self) -> None:
        import httpx

        last_id = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10)
        ) as client:
            while True:
                try:
                    params = {"timeout": 50, "limit": 10}
                    if last_id > 0:
                        params["offset"] = last_id + 1
                    r = await client.get(
                        f"https://api.telegram.org/bot{settings.telegram_token}/getUpdates",
                        params=params,
                    )
                    data = r.json()
                    if data.get("ok"):
                        for upd in data.get("result", []):
                            uid = upd.get("update_id", 0)
                            if uid > last_id:
                                last_id = uid
                            await self._process_raw_update(upd)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Poll error: %s", e)
                    await asyncio.sleep(5)

    async def _process_raw_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        user = msg.get("from", {})
        import time as _time

        _ts = _time.strftime("%H:%M:%S") + ".%03d" % int((_time.time() % 1) * 1000)

        msg_type = "text"
        if msg.get("voice"):
            msg_type = "voice"
        elif msg.get("photo"):
            msg_type = "photo"
        elif msg.get("document"):
            msg_type = "document"

        logger.info(
            "[TELEMETRY A] Mensaje recibido [%s] [%s] chat=%d user=@%s type=%s",
            _ts,
            msg_type,
            chat_id,
            user.get("username", "?"),
            msg_type,
        )

        from telegram import Update

        assert self._app is not None
        bot = self._app.bot
        update = Update.de_json(upd, bot)
        await self._app.process_update(update)

    async def stop(self) -> None:
        if hasattr(self, "_polling_task") and self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        if self._app:
            try:
                await self._app.stop()
            except Exception:
                pass
            logger.info("RafitaBot stopped")

    async def destroy(self) -> None:
        if self._app:
            try:
                await self._app.shutdown()
            except Exception:
                pass
            logger.info("RafitaBot destroyed")

    async def send_proactive_message(self, chat_id: int, text: str) -> bool:
        if not self._app:
            logger.warning("Bot not initialized, cannot send proactive message")
            return False
        try:
            await self._app_started.wait()
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info("Proactive message sent to chat %d", chat_id)
            return True
        except Exception as e:
            logger.warning(
                "Failed to send proactive message to chat %d: %s",
                chat_id,
                e,
            )
            return False

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, disable_web_page_preview=True)

    @staticmethod
    async def _reply_markdown(update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                text, parse_mode="Markdown", disable_web_page_preview=True
            )

    @staticmethod
    async def _reply_html(update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                text, parse_mode="HTML", disable_web_page_preview=True
            )


bot = RafitaBot()
