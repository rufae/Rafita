"""Secuencias de email con personalizacion dinamica (Fase 2, 2026-09-29).

Una secuencia es un plan de emails escalonados para un contacto (p. ej. una
propuesta): dia 0 presentacion, dia 3 seguimiento, dia 7 ultimo aviso. Cada
envio se redacta con el LLM usando el contexto del contacto y la secuencia se
**detiene sola si el contacto responde**. Todo se registra en la nota CRM.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.logger import logger

PASOS_POR_DEFECTO = [
    (0, "presentacion o agradecimiento inicial"),
    (3, "seguimiento amable recordando el asunto"),
    (7, "ultimo recordatorio, sin presion, dejando la puerta abierta"),
]
MAX_ENVIOS_POR_EJECUCION = 3
ESTADOS = ("active", "paused", "stopped", "done")


def _hoy_iso() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


def _parse_pasos(pasos: Any) -> list[tuple[int, str]]:
    """Acepta [(dias, objetivo)], [{"delay_days":..,"goal":..}] o '0:algo,3:otro'."""
    if not pasos:
        return list(PASOS_POR_DEFECTO)
    resultado: list[tuple[int, str]] = []
    if isinstance(pasos, str):
        for trozo in pasos.split(","):
            if ":" in trozo:
                dias, objetivo = trozo.split(":", 1)
                try:
                    resultado.append((int(dias.strip()), objetivo.strip()))
                except ValueError:
                    continue
        return resultado or list(PASOS_POR_DEFECTO)
    for paso in pasos:
        if isinstance(paso, dict):
            try:
                dias = int(paso.get("delay_days", paso.get("dias", 0)) or 0)
            except (TypeError, ValueError):
                dias = 0
            objetivo = str(paso.get("goal", paso.get("objetivo", "")) or "").strip()
            if objetivo:
                resultado.append((max(0, dias), objetivo))
    return resultado or list(PASOS_POR_DEFECTO)


async def _nota_crm(nombre: str, texto: str) -> None:
    try:
        from src.services import crm_service

        if await crm_service.obtener(nombre):
            await crm_service.anadir_nota(nombre, texto)
    except Exception as e:
        logger.debug("Secuencias: no se pudo anotar en CRM: %s", e)


async def crear_secuencia(
    nombre: str,
    contacto: str,
    email: str,
    contexto: str = "",
    pasos: Any = None,
) -> dict[str, Any]:
    nombre = (nombre or "").strip()
    contacto = (contacto or "").strip()
    email = (email or "").strip()
    if not nombre or not contacto or "@" not in email:
        return {
            "success": False,
            "message": "Necesito nombre de la secuencia, contacto y email valido.",
        }
    plan = _parse_pasos(pasos)
    sequence_id = await db.create_sequence(nombre, contacto, email, contexto or "")
    for indice, (dias, objetivo) in enumerate(plan):
        await db.add_sequence_step(sequence_id, indice, dias, objetivo)
    try:
        from src.services import crm_service

        cliente = await crm_service.obtener(contacto)
        if cliente:
            await crm_service.crear_o_actualizar(contacto, email=email)
        else:
            await crm_service.crear_o_actualizar(
                contacto, email=email, estado="contactado", proximo_paso="Secuencia de emails"
            )
        await crm_service.anadir_nota(
            contacto, "Secuencia '%s' creada (%d emails)" % (nombre, len(plan))
        )
    except Exception as e:
        logger.debug("Secuencias: CRM no disponible: %s", e)
    detalle = ", ".join("dia %d: %s" % (d, o) for d, o in plan)
    return {
        "success": True,
        "id": sequence_id,
        "message": "Secuencia *%s* creada para %s (%s): %s" % (nombre, contacto, email, detalle),
    }


async def listar_secuencias() -> dict[str, Any]:
    secuencias = await db.list_sequences()
    if not secuencias:
        return {"success": True, "message": "No hay secuencias de email creadas."}
    lineas = ["✉️ *Secuencias de email*"]
    for seq in secuencias:
        pasos = await db.list_sequence_steps(seq["id"])
        enviados = sum(1 for p in pasos if p["status"] == "sent")
        lineas.append(
            "  • #%d *%s* — %s (%s) [%s] %d/%d enviados"
            % (
                seq["id"],
                seq["name"],
                seq["contact_name"],
                seq["contact_email"],
                seq["status"],
                enviados,
                len(pasos),
            )
        )
    return {"success": True, "message": "\n".join(lineas), "sequences": secuencias}


async def parar_secuencia(sequence_id: int, motivo: str = "manual") -> dict[str, Any]:
    seq = await db.get_sequence(int(sequence_id))
    if not seq:
        return {"success": False, "message": "No existe la secuencia #%s." % sequence_id}
    await db.update_sequence_status(int(sequence_id), "stopped")
    await _nota_crm(seq["contact_name"], "Secuencia '%s' detenida (%s)" % (seq["name"], motivo))
    return {
        "success": True,
        "message": "Secuencia *%s* detenida (%s)." % (seq["name"], motivo),
    }


async def reanudar_secuencia(sequence_id: int) -> dict[str, Any]:
    seq = await db.get_sequence(int(sequence_id))
    if not seq:
        return {"success": False, "message": "No existe la secuencia #%s." % sequence_id}
    await db.update_sequence_status(int(sequence_id), "active")
    return {"success": True, "message": "Secuencia *%s* reactivada." % seq["name"]}


async def _respondio_contacto(email: str, desde_iso: str) -> bool:
    """True si el contacto ha escrito desde que empezo la secuencia."""
    try:
        from src.services.google_services_manager import google_services

        if not google_services.is_ready:
            return False
        desde = datetime.fromisoformat(desde_iso[:10])
        dias = max(1, (datetime.now() - desde).days + 1)
        resultado = await google_services.search_gmail(
            "from:%s newer_than:%dd" % (email, dias), max_results=1
        )
        return bool(resultado.get("messages"))
    except Exception as e:
        logger.warning("Secuencias: no se pudo comprobar respuesta de %s: %s", email, e)
        return False


async def _componer_email(
    seq: dict[str, Any], step: dict[str, Any], historial: str
) -> tuple[str, str]:
    """Redacta asunto y cuerpo con el LLM a partir del contexto y objetivo."""
    from src.ollama_client import llm

    sistema = (
        "Redactas correos en espanol, profesionales y cercanos. Devuelve SOLO el "
        "correo: primera linea 'Asunto: <asunto>' y despues el cuerpo. Sin "
        "Markdown, sin explicaciones. Firma con el nombre del remitente si lo "
        "conoces; si no, con un cierre cordial."
    )
    peticion = (
        "Contacto: %s (%s).\nContexto: %s\nObjetivo de este email (paso %d): %s\n"
        "Emails ya enviados: %s\nEscribe el correo."
        % (
            seq.get("contact_name", ""),
            seq.get("contact_email", ""),
            seq.get("context", "") or "(sin contexto adicional)",
            int(step.get("step_no", 0)) + 1,
            step.get("goal", ""),
            historial or "ninguno",
        )
    )
    try:
        respuesta = await llm.chat(
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": peticion},
            ],
            max_tokens=700,
        )
    except Exception as e:
        logger.warning("Secuencias: LLM no disponible (%s); usando plantilla", e)
        respuesta = ""
    lineas = [ln for ln in (respuesta or "").splitlines() if ln.strip()]
    subject = "Seguimiento: %s" % seq.get("name", "")
    if lineas and lineas[0].lower().startswith("asunto:"):
        subject = lineas[0].split(":", 1)[1].strip() or subject
        cuerpo = "\n".join(lineas[1:]).strip()
    else:
        cuerpo = "\n".join(lineas).strip()
    if not cuerpo:
        cuerpo = "Hola %s:\n\nTe escribo en relacion con %s. %s\n\nQuedo a tu disposicion." % (
            seq.get("contact_name", ""),
            seq.get("context", "") or seq.get("name", ""),
            step.get("goal", ""),
        )
    return subject, cuerpo


async def ejecutar_secuencias(
    dry_run: bool = False, max_envios: int = MAX_ENVIOS_POR_EJECUCION
) -> dict[str, Any]:
    """Detiene secuencias con respuesta y envia los pasos vencidos."""
    from src.services.google_services_manager import google_services

    paradas: list[str] = []
    enviados: list[dict[str, Any]] = []

    for seq in await db.list_sequences(status="active"):
        if await _respondio_contacto(seq["contact_email"], seq["created_at"]):
            await db.update_sequence_status(seq["id"], "stopped")
            await _nota_crm(
                seq["contact_name"], "Secuencia '%s' detenida: el contacto respondio" % seq["name"]
            )
            paradas.append(seq["name"])

    for step in await db.due_sequence_steps():
        if len(enviados) >= max(1, int(max_envios)):
            break
        seq = await db.get_sequence(step["sequence_id"])
        if not seq or seq["status"] != "active":
            continue
        historial = "\n".join(
            "  - %s (%s)" % (p.get("subject") or "?", p.get("sent_at") or "")
            for p in await db.list_sequence_steps(seq["id"])
            if p["status"] == "sent"
        )
        subject, body = await _componer_email(seq, step, historial)
        if dry_run:
            await db.update_sequence_step(step["step_id"], "pending", subject, body)
            enviados.append(
                {
                    "sequence": seq["name"],
                    "step": step["step_no"],
                    "subject": subject,
                    "dry_run": True,
                }
            )
            continue
        if not google_services.is_ready:
            logger.warning("Secuencias: Google no conectado; no se envia")
            break
        resultado = await google_services.send_email(
            to=seq["contact_email"], subject=subject, body=body
        )
        if not resultado.get("success"):
            logger.warning("Secuencias: fallo al enviar a %s: %s", seq["contact_email"], resultado)
            continue
        await db.update_sequence_step(
            step["step_id"], "sent", subject, body, message_id=str(resultado.get("id", ""))
        )
        await _nota_crm(
            seq["contact_name"],
            "Email %d/%s enviado: %s"
            % (step["step_no"] + 1, "secuencia '%s'" % seq["name"], subject),
        )
        enviados.append(
            {
                "sequence": seq["name"],
                "step": step["step_no"],
                "subject": subject,
                "to": seq["contact_email"],
            }
        )

    cambios = len(paradas) + len(enviados)
    if not cambios:
        return {"success": True, "changes": 0, "sent": [], "stopped": [], "message": ""}
    partes = []
    if enviados:
        if dry_run:
            partes.append("%d email(s) redactados (dry run, sin enviar)" % len(enviados))
        else:
            partes.append("%d email(s) enviados" % len(enviados))
    if paradas:
        partes.append("%d secuencia(s) detenidas por respuesta" % len(paradas))
    lineas = ["✉️ *Secuencias de email:* " + ", ".join(partes)]
    for envio in enviados:
        lineas.append(
            "  • %s — paso %d: %s" % (envio["sequence"], envio["step"] + 1, envio["subject"])
        )
    for nombre in paradas:
        lineas.append("  • %s — el contacto respondio (secuencia detenida)" % nombre)
    return {
        "success": True,
        "changes": cambios,
        "sent": enviados,
        "stopped": paradas,
        "dry_run": dry_run,
        "message": "\n".join(lineas),
    }


async def handle(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Entrada del tool `manage_sequences` del chat."""
    action = (action or "").strip().lower()
    if action == "create":
        return await crear_secuencia(
            str(args.get("nombre", "") or ""),
            str(args.get("contacto", "") or ""),
            str(args.get("email", "") or ""),
            str(args.get("contexto", "") or ""),
            args.get("pasos"),
        )
    if action == "list":
        return await listar_secuencias()
    if action == "stop":
        return await parar_secuencia(int(args.get("sequence_id", 0) or 0))
    if action == "resume":
        return await reanudar_secuencia(int(args.get("sequence_id", 0) or 0))
    if action == "run":
        return await ejecutar_secuencias(dry_run=bool(args.get("dry_run", False)))
    return {
        "success": False,
        "message": "Accion no valida. Usa: create, list, stop, resume, run.",
    }
