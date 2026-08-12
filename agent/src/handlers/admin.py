from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.database import db
from src.logger import logger


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


async def setup_google_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    cred_file = CREDENTIALS_DIR / "credentials.json"
    service_file = CREDENTIALS_DIR / "service_account.json"

    if cred_file.exists():
        await message.reply_text(
            "✅ *Google Calendar ya está configurado*"
            "\n\nArchivo encontrado: `credentials.json`"
            "\n\nUsa `/status` para verificar el panel de control."
            "\nSi necesitas reemplazar las credenciales, borra el archivo manualmente"
            " y vuelve a ejecutar este comando.",
            parse_mode="Markdown",
        )
        logger.info("setup_google: credentials already present for user %d", user.id)
        return

    if service_file.exists():
        await message.reply_text(
            "✅ *Google Calendar configurado via service account*"
            "\n\nArchivo encontrado: `service_account.json`"
            "\n\nUsa `/status` para verificar el panel de control.",
            parse_mode="Markdown",
        )
        return

    admin_ids = settings.admin_ids
    if admin_ids and user.id not in admin_ids:
        await message.reply_text(" Solo los administradores pueden configurar Google Calendar.")
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
        "Una vez colocado, ejecuta `/setup_google` de nuevo o reinicia el bot."
    )

    await message.reply_text(instructions, parse_mode="Markdown")
    logger.info("setup_google: instructions sent to user %d", user.id)
