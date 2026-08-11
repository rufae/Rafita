from telegram import Update
from telegram.ext import ContextTypes

from src.database import db
from src.logger import logger


async def modo_voz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    chat_id = user.id
    args = context.args

    if not args:
        current = await db.get_preference(chat_id, "voice_replies", False)
        estado = "activado" if current else "desactivado"
        await message.reply_text(
            f"Modo voz respuesta: {estado}\n"
            f"Usa /modo_voz on para activar respuestas de voz\n"
            f"Usa /modo_voz off para desactivarlas"
        )
        return

    arg = args[0].lower()
    if arg in ("on", "1", "true", "si"):
        await db.set_preference(chat_id, "voice_replies", True)
        await message.reply_text(
            "Modo voz respuesta activado. Cuando envies notas de voz, "
            "recibiras respuestas con audio sintetico."
        )
        logger.info("Voice replies enabled for user %d", chat_id)
    elif arg in ("off", "0", "false", "no"):
        await db.set_preference(chat_id, "voice_replies", False)
        await message.reply_text(
            "Modo voz respuesta desactivado. Las respuestas volveran a ser texto."
        )
        logger.info("Voice replies disabled for user %d", chat_id)
    else:
        await message.reply_text(
            "Usa: /modo_voz on  o  /modo_voz off"
        )
