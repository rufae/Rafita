import json
import os
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.database import db
from src.logger import logger
from src.services.google_service import google_service


async def evento_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    args = context.args
    if not args or len(args) < 2:
        await message.reply_text(
            "Usa: /evento <fecha> <título> [descripción]\n\n"
            "Formato de fecha: YYYY-MM-DD HH:MM\n"
            "Ejemplo: `/evento 2026-12-25 18:00 Cena navideña Con la familia`"
        )
        return

    date_str = f"{args[0]} {args[1]}"
    title_start_idx = 2

    try:
        event_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            date_part = args[0]
            time_part = "23:59"
            event_dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
            title_start_idx = 1
        except ValueError:
            await message.reply_text(
                "Formato de fecha inválido. Usa: YYYY-MM-DD HH:MM\n"
                "Ejemplo: `/evento 2026-12-25 18:00 Cena navideña`"
            )
            return

    if len(args) <= title_start_idx:
        await message.reply_text("Debes proporcionar al menos un título para el evento.")
        return

    title = args[title_start_idx]
    description = " ".join(args[title_start_idx + 1 :]) if len(args) > title_start_idx + 1 else None

    event_id = await db.add_event(
        chat_id=user.id,
        title=title,
        event_datetime=event_dt.strftime("%Y-%m-%d %H:%M:%S"),
        description=description,
    )

    logger.info(
        "Event created: user=%d title=%s datetime=%s id=%d",
        user.id,
        title,
        event_dt,
        event_id,
    )

    await message.reply_text(
        f"✅ Evento creado:\n"
        f"   • Título: {title}\n"
        f"   • Fecha: {event_dt.strftime('%d/%m/%Y %H:%M')}\n"
        + (f"   • Descripción: {description}\n" if description else "")
        + f"   • ID: {event_id}"
    )


async def eventos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    events = await db.get_upcoming_events(user.id)

    if not events:
        await message.reply_text("No tienes eventos próximos.")
        return

    lines = ["📅 *Eventos próximos:*\n"]
    for ev in events:
        ev_dt = datetime.strptime(ev["event_datetime"], "%Y-%m-%d %H:%M:%S")
        lines.append(
            f"• *{ev['title']}*"
            + f"\n   📆 {ev_dt.strftime('%d/%m/%Y %H:%M')}"
            + (f"\n   📝 {ev['description']}" if ev["description"] else "")
            + f"\n   🆔 {ev['id']}"
        )
        lines.append("")

    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def alerta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    args = context.args
    if not args:
        await message.reply_text(
            "Usa: /alerta <mensaje> [tipo] [expira:YYYY-MM-DD]\n\n"
            "Tipos: info (default), warning, urgent\n"
            "Ejemplo: `/alerta Revisar presupuesto mensual warning expira:2026-07-01`"
        )
        return

    alert_text_parts = []
    alert_type = "info"
    expires_at: str | None = None

    for arg in args:
        if arg.startswith("expira:"):
            try:
                exp_date = datetime.strptime(arg.split(":", 1)[1], "%Y-%m-%d")
                expires_at = exp_date.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                await message.reply_text("Formato de expiración inválido. Usa: expira:YYYY-MM-DD")
                return
        elif arg in ("info", "warning", "urgent"):
            alert_type = arg
        else:
            alert_text_parts.append(arg)

    if not alert_text_parts:
        await message.reply_text("Debes proporcionar un mensaje para la alerta.")
        return

    alert_text = " ".join(alert_text_parts)

    alert_id = await db.add_alert(
        chat_id=user.id,
        message=alert_text,
        alert_type=alert_type,
        expires_at=expires_at,
    )

    type_emoji = {"info": "ℹ️", "warning": "⚠️", "urgent": "🚨"}
    emoji = type_emoji.get(alert_type, "ℹ️")

    logger.info(
        "Alert created: user=%d type=%s id=%d",
        user.id,
        alert_type,
        alert_id,
    )

    await message.reply_text(
        f"{emoji} Alerta creada:\n"
        f"   • Mensaje: {alert_text}\n"
        f"   • Tipo: {alert_type}\n"
        + (f"   • Expira: {expires_at}\n" if expires_at else "")
        + f"   • ID: {alert_id}"
    )


async def alertas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    alerts = await db.get_active_alerts(user.id)

    if not alerts:
        await message.reply_text("No tienes alertas activas.")
        return

    type_emoji = {"info": "ℹ️", "warning": "⚠️", "urgent": "🚨"}

    lines = ["🔔 *Alertas activas:*\n"]
    for alert in alerts:
        emoji = type_emoji.get(alert["alert_type"], "ℹ️")
        lines.append(
            f"{emoji} *[{alert['alert_type'].upper()}]* {alert['message']}"
            + f"\n   🆔 {alert['id']}"
            + (f"\n   📅 Creada: {alert['created_at']}" if alert["created_at"] else "")
        )
        lines.append("")

    lines.append("Usa /alerta <id> para marcar como leída.")

    await message.reply_text("\n".join(lines), parse_mode="Markdown")


CREDENTIALS_DIR = Path("/workspace/credentials")


async def calendario_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fija el calendario de Google a usar (tarea 3.9). Solo administradores."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    admin_ids = settings.admin_ids
    if admin_ids and user.id not in admin_ids:
        await message.reply_text("Solo los administradores pueden cambiar el calendario.")
        return
    args = context.args or []
    if not args:
        await message.reply_text(
            "Calendario actual: %s\n\nPara fijarlo:\n/calendario tu-correo@gmail.com\n\n"
            "Antes debes compartir ese calendario con "
            "rafita@rafita-500317.iam.gserviceaccount.com (permiso «Hacer cambios en "
            "los eventos»)." % google_service.calendar_id
        )
        return
    result = await google_service.set_calendar_id(args[0])
    await message.reply_text(result.get("message", "Resultado desconocido."))


async def setup_google_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    admin_ids = settings.admin_ids
    if admin_ids and user.id not in admin_ids:
        await message.reply_text("Solo los administradores pueden configurar Google Calendar.")
        return

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    cred_file = CREDENTIALS_DIR / "credentials.json"
    service_file = CREDENTIALS_DIR / "service_account.json"
    args = context.args or []

    # Si subieron una cuenta de servicio con el nombre de OAuth, renombrarla.
    if cred_file.exists():
        try:
            data = json.loads(cred_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if data.get("type") == "service_account":
            os.replace(cred_file, service_file)
            logger.info("setup_google: cuenta de servicio detectada y renombrada")

    if service_file.exists():
        sa_email = ""
        try:
            sa_email = json.loads(service_file.read_text(encoding="utf-8")).get("client_email", "")
        except Exception:
            pass
        await message.reply_text(
            "✅ Cuenta de servicio detectada.\n\n"
            "Email de la cuenta: %s\n\n"
            "Para que pueda leer y crear eventos:\n"
            "1. Abre Google Calendar en el navegador → tu calendario → "
            "«Compartir con determinadas personas» → añade ese email con "
            "permiso «Hacer cambios en los eventos».\n"
            "2. Reinicia el bot.\n\n"
            "Detectará tu calendario automáticamente (no hace falta que me "
            "des tu correo). Si no lo detecta, avisará en los logs." % (sa_email or "(no legible)")
        )
        return

    if cred_file.exists():
        # Flujo determinista (sin depender del modelo):
        #   /setup_google            -> estado o enlace de autorizacion
        #   /setup_google <codigo>   -> intercambia el codigo por el token
        if args:
            code = " ".join(args).strip()
            result = await google_service.exchange_code(code)
            await message.reply_text(result.get("message", "Resultado desconocido."))
            logger.info("setup_google: codigo intercambiado para user %d", user.id)
            return

        if await google_service.initialize():
            await message.reply_text(
                "✅ Google Calendar ya está conectado y funcionando.\n\n"
                "Prueba a pedirme que cree un evento o usa /eventos."
            )
            return

        result = await google_service.generate_auth_url()
        if result.get("success"):
            await message.reply_text(
                "Para conectar tu Google Calendar:\n\n"
                "1. Abre este enlace en tu navegador y autoriza la aplicación:\n"
                "%s\n\n"
                "2. Google te dará un código. Envíamelo así:\n"
                "/setup_google <codigo>\n\n"
                "(El código caduca pronto; si falla, repite /setup_google para "
                "obtener un enlace nuevo.)" % result["auth_url"]
            )
        else:
            await message.reply_text(
                "No se pudo generar el enlace de autorización: %s"
                % result.get("message", "error desconocido")
            )
        logger.info("setup_google: enlace de autorizacion enviado a user %d", user.id)
        return

    instructions = (
        "*Configuracion de Google Calendar*\n\n"
        "Para conectar con tu Google Calendar real necesito un archivo de credenciales.\n\n"
        "*Opcion 1 (recomendada): Cuenta de servicio*\n"
        "1. Ve a https://console.cloud.google.com/apis/credentials\n"
        "2. Crea una cuenta de servicio y descarga su JSON\n"
        "3. Renombralo a `service_account.json`\n"
        "4. Colocalo en la carpeta `credentials/` dentro de `RafAI/` en tu PC\n"
        "   (se sincroniza automaticamente con el contenedor)\n\n"
        "*Opcion 2: OAuth Desktop*\n"
        "1. Descarga tu `credentials.json` de Google Cloud Console\n"
        "2. Enviamelo AQUI como archivo adjunto en este chat\n"
        "   (lo guardare automaticamente en la carpeta correcta)\n\n"
        "Despues ejecuta `/setup_google` para recibir el enlace de autorizacion "
        "y completa con `/setup_google <codigo>`."
    )

    await message.reply_text(instructions, parse_mode="Markdown")
    logger.info("setup_google: instructions sent to user %d", user.id)
