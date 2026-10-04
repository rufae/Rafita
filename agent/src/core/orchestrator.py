"""Core orchestrator — shared AI response pipeline for Telegram and Voice.

Both the Telegram bot (chat.py) and the voice stream (voice_stream/server.py)
consume this module to ensure consistent behavior: same system prompt, same
tool definitions, same RAG access (search_second_brain), same model.

This avoids duplicating the prompt/rules/tools logic across interfaces.
"""

import asyncio
import json
import re
import time as _time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.handlers.chat_tools import (
    TOOLS_DEFINITIONS,
    best_tools_for_message,
    select_tools_semantic,
)
from src.i18n import language_name, language_rule, reply_instruction
from src.logger import logger
from src.models.schemas import MessageRole
from src.ollama_client import OllamaClientError, llm
from src.services.google_services_manager import google_services
from src.utils.citations import citations, finalize_citations, strip_citation_marks
from src.utils.telemetry import get_correlation_id, metrics
from src.vault_config import get_taxonomy

# Afirmaciones de accion sin herramienta (alucinaciones 2026-09-29: la llamada
# dijo "Buscando un correo..." sin buscar y el chat dijo "He guardado la tarea"
# sin guardarla). Si el modelo afirma una accion y no hubo tool_calls, se
# reintenta una vez obligandole a usar la herramienta o a desdecirse.
# Verbos de accion en primera persona ("he guardado", "he borrado"...). Se
# aceptan variantes/typos con la raiz ("he marcan", "he programado"): el
# 2026-09-30 el modelo afirmo tareas borradas y correos enviados que nunca
# ocurrieron y la lista anterior no cubria eliminar/programar/marcar.
_ACTION_CLAIM_RE = re.compile(
    r"\b(?:"
    r"he (?:guard|cre|a[nñ]ad|apunt|anot|tom|envi|complet|registr|encontr|"
    r"elimin|borr|marc|program|actualiz|mov|reserv|cancel|dej|puest)\w*"
    r"|tom[oé] nota"
    r"|(?:guardando|apuntando|anotando|completando|enviando|eliminando|borrando)"
    r"|aqu[ií] tienes|te muestro|te busco|un momento"
    r"|resultados?:"
    r"|busc\w+"
    r"|b[uú]squed\w*"
    r"|search\w*"
    r"|encontr[eé]"
    r")\b",
    re.IGNORECASE,
)
# Negacion delante de la supuesta accion ("no he encontrado", "sin resultados",
# "no puedo buscar"): es honestidad, no alucinacion.
_NEGACION_RE = re.compile(r"\b(?:no|sin|nunca|tampoco)\b[^.!?]{0,24}$", re.IGNORECASE)
# Correo electronico en la respuesta: si no lo dijo el usuario, es inventado.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Umbrales de similitud (bge-m3, calibrados con mensajes reales el
# 2026-09-30: small talk <= 0.44; acciones 0.51-0.66).
TOOL_RETRY_MIN_SCORE = 0.45
TOOL_FORCE_MIN_SCORE = 0.50
# Con confianza alta se ofrece SOLO esa herramienta; si no, todas con un
# aviso duro (el top-1 puede ser otra parecida: p.ej. "borra el evento" daba
# create_google_calendar_event 0.55 y manage_google_calendar 0.58).
TOOL_CONFIDENT_SCORE = 0.60

HONEST_FALLBACK = (
    "No he podido comprobar esa informacion con mis herramientas y no quiero "
    "darte datos inventados. ¿Puedes darme mas detalle o lo intento de otra forma?"
)

# Saludo generico ("Hola, soy Rafita. ¿En que puedo ayudarte?"): si aparece
# como respuesta a una pregunta real (sobre todo tras usar herramientas), es
# que el modelo se ha perdido (bug 2026-09-30 con la lista de Drive).
_SALUDO_GENERICO_RE = re.compile(
    r"^\s*[¡!]?\s*(?:hola|buenas|buenos\s+d[ií]as)[\s,!.¡¿]*(?:soy\s+rafita|"
    r"en\s+qu[eé]\s+puedo\s+ayudarte)",
    re.IGNORECASE,
)
# Saludo generico en la RESPUESTA del modelo: cualquier frase que abra con
# "hola/buenas/buenos días" tras haber llamado una herramienta. Más ancha que
# _es_saludo_generico (que exige "soy rafita"/"en qué puedo ayudarte"): en
# 2026-10-04 gemma respondió "¡Hola! Soy tu asistente personal..." al final
# de una pregunta con datos y el guard no la cazó.
_ABRE_CON_SALUDO_RE = re.compile(
    r"^\s*[¡!]?\s*(?:hola|buenas(?:\s+tardes|\s+noches)?|buen(?:os)?\s+d[ií]as|hey)\b",
    re.IGNORECASE,
)

# Negacion falsa tras una herramienta que SI devolvio datos (2026-09-30:
# list_google_drive respondio con la lista y el modelo dijo "no tengo acceso").
_NEGACION_FALSA_RE = re.compile(
    r"(?:no\s+tengo\s+acceso|no\s+puedo\s+acceder|no\s+tengo\s+(?:esa|la)\s+"
    r"informaci[oó]n|no\s+dispongo\s+de|no\s+me\s+es\s+posible\s+acceder"
    r"|no\s+he\s+podido\s+obtener|no\s+se\s+han?\s+proporcionado"
    r"|no\s+se\s+proporcionaron|no\s+he\s+recibido|no\s+(?:me\s+)?ha\s+devuelto"
    r"|no\s+hay\s+(?:resultados|archivos|carpetas|elementos|documentos|datos)"
    r"|no\s+se\s+han\s+encontrado|no\s+se\s+encontr[oó]"
    r"|no\s+he\s+encontrado|no\s+encontr[eé]|no\s+dispongo"
    r"|no\s+se\s+ha\s+ejecutado\s+la\s+herramienta|no\s+puedo\s+(?:listar|mostrar)"
    r"|necesito\s+consultar|un\s+momento\s+mientras|mientras\s+accedo"
    r"|voy\s+a\s+(?:consultar|acceder|buscar)"
    r")",
    re.IGNORECASE,
)


def _es_saludo_generico(texto: str | None) -> bool:
    return bool(_SALUDO_GENERICO_RE.match((texto or "").strip()))


def _abre_con_saludo(texto: str | None) -> bool:
    return bool(_ABRE_CON_SALUDO_RE.match((texto or "").strip()))


def _es_saludo_solo(texto: str | None) -> bool:
    """El USUARIO solo saluda (sin pregunta ni encargo).

    En ese caso un modelo que contesta con un saludo no es una desviación.
    Si el mensaje trae '?' o es largo, es una pregunta real y el guard de
    respuestas desviadas debe seguir activo aunque empiece por "hola".
    """
    t = (texto or "").strip()
    return bool(_ABRE_CON_SALUDO_RE.match(t)) and "?" not in t and len(t) <= 40


def _es_negacion_falsa(texto: str | None) -> bool:
    return bool(_NEGACION_FALSA_RE.search(texto or ""))


# Plantillas inventadas tipo "[Nombre de la carpeta 1]" (2026-09-30: gemma
# relleno una tabla falsa en vez de usar la lista real de Drive).
_PLACEHOLDER_RE = re.compile(
    r"\[(?:nombre|id|dato|fecha|hora|importe|asunto|carpeta|archivo|valor)"
    r"[^\]]{0,40}\]",
    re.IGNORECASE,
)


def _tiene_placeholders(texto: str | None) -> bool:
    return bool(_PLACEHOLDER_RE.search(texto or ""))


def _respuesta_desviada(texto: str | None) -> bool:
    """Saludo, negacion falsa o plantilla inventada en vez de los datos."""
    return (
        _abre_con_saludo(texto)
        or _es_saludo_generico(texto)
        or _es_negacion_falsa(texto)
        or _tiene_placeholders(texto)
    )


_ITEM_LISTA_RE = re.compile(r"[•]\s*[^\w\n]*([\w\u00c0-\u024f][\w\u00c0-\u024f .\-]{2,30})")


def _tool_lista_ignorada(messages: list[dict[str, Any]], content: str | None) -> bool:
    """True si la tool devolvio una lista y la respuesta no menciona NINGUN item.

    2026-09-30: gemma respondia negaciones/plantillas ("no se han recibido
    resultados", "[Nombre de la carpeta 1]") con la lista real delante. Esto
    es determinista: si no aparece ni un nombre, se usa el mensaje de la tool.
    """
    mensaje = _ultimo_mensaje_tool(messages)
    if not mensaje or "•" not in mensaje:
        return False
    nombres = [m.group(1).strip() for m in _ITEM_LISTA_RE.finditer(mensaje)]
    nombres = [n for n in nombres if len(n) >= 3][:6]
    if not nombres:
        return False
    bajo = (content or "").lower()
    return not any(n.lower() in bajo for n in nombres)


def _hay_tool_con_datos(messages: list[dict[str, Any]]) -> bool:
    """True si alguna herramienta devolvio DATOS reales (no un 'no hay...').

    Se usa para las guardias de saludo/negacion falsa: si la tool no trajo
    nada, una negativa del modelo es correcta y no hay que reintentar.
    """
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            datos = json.loads(m.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(datos, dict) or datos.get("success") is False:
            continue
        mensaje = str(datos.get("message") or "").strip()
        if len(mensaje) > 20 and not re.match(r"(?:no|sin)\s", mensaje, re.IGNORECASE):
            return True
    return False


def _hay_tool_results(messages: list[dict[str, Any]]) -> bool:
    """True si alguna herramienta se ejecuto y devolvio resultado (aunque sea
    'no hay datos').

    2026-10-03: si el modelo respondia un saludo tras una tool que no trajo
    datos ("No hay eventos..."), _hay_tool_con_datos era False y el saludo se
    colaba. Con cualquier resultado de tool, el fallback determinista usa el
    mensaje real de la herramienta.
    """
    return any(m.get("role") == "tool" for m in messages)


def _ultimo_mensaje_tool(messages: list[dict[str, Any]]) -> str:
    """Ultimo mensaje legible de una herramienta (para fallback determinista)."""
    for m in reversed(messages):
        if m.get("role") != "tool":
            continue
        try:
            datos = json.loads(m.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(datos, dict):
            mensaje = datos.get("message")
            if isinstance(mensaje, str) and mensaje.strip():
                return mensaje.strip()
    return ""


def _hallucination_risk(content: str, user_text: str) -> bool:
    """True si la respuesta afirma acciones o cita correos que no vinieron de tools."""
    texto = content or ""
    for match in _ACTION_CLAIM_RE.finditer(texto):
        prefix = texto[max(0, match.start() - 40) : match.start()]
        if _NEGACION_RE.search(prefix):
            continue
        return True
    texto_usuario = (user_text or "").lower()
    return any(match.group().lower() not in texto_usuario for match in _EMAIL_RE.finditer(texto))


SYSTEM_PROMPT_VOICE = (
    f"{language_rule()}"
    "SECOND_BRAIN_RULE: Tienes acceso al segundo cerebro del usuario a traves de "
    "search_second_brain. DEBES usarlo antes de responder cualquier pregunta que "
    "pueda estar relacionada con informacion personal del usuario: proyectos, "
    "finanzas, notas tecnicas, diario, ideas, apuntes. NO improvises datos "
    "personales. Siempre cita la fuente exacta de la nota (note_path) en tu "
    "respuesta para que el usuario pueda verificar. Si no encuentras nada en "
    "el segundo cerebro, dilo claramente y ofrece buscar en internet.\n"
    "PROACTIVE_BRAIN_RULE: Eres el guardian del segundo cerebro. Cuando en una "
    "conversacion el usuario mencione una IDEA NUEVA, una DECISION IMPORTANTE, "
    "un DATO PERSONAL RELEVANTE, o un APRENDIZAJE TECNICO, debes guardarlo "
    "PROACTIVAMENTE en Obsidian usando manage_obsidian_note (create) o "
    "ingest_file SIN que el usuario te lo pida. Usa estas carpetas:\n"
    f"- Ideas y conceptos nuevos -> {get_taxonomy().path('zettelkasten')}/ (tipo: nota-atomica)\n"
    f"- Decisiones de proyecto -> {get_taxonomy().path('projects')}/ (tipo: proyecto)\n"
    f"- Datos personales (salud, preferencias) -> {get_taxonomy().path('areas')}/ (tipo: area)\n"
    f"- Aprendizajes tecnicos -> {get_taxonomy().path('resources')}/ (tipo: recurso)\n"
    "Confirma brevemente: 'He guardado esto en tu segundo cerebro'.\n"
    f"Eres {settings.assistant_name}, un asistente virtual personal. "
    f"Responde en {language_name()} de forma conversacional, clara y concisa. "
    "Tienes acceso a herramientas que debes invocar automaticamente cuando "
    "el usuario lo necesite.\n\n"
    "Herramientas disponibles:\n"
    "- search_second_brain (busqueda semantica con citacion)\n"
    "- ask_deep_knowledge_base (segundo cerebro completo)\n"
    "- save_expense / get_finance_summary\n"
    "- manage_obsidian_note / search_obsidian_vault\n"
    "- search_web / remember_fact / search_knowledge\n"
    "- create_event / create_alert / manage_google_calendar\n"
    "- manage_google_tasks (tareas: guardar, listar, completar)\n"
    "- get_weather (tiempo) / get_alerts (alertas pendientes)\n"
    "- search_gmail / send_gmail (correo)\n"
    "- find_contact (telefonos y correos de contactos)\n"
    "- manage_crm (clientes y pipeline)\n"
    "- search_google_drive / read_google_drive_file\n"
    "- generate_google_auth_link / save_google_verification_code\n\n"
    "Cuando invoques una herramienta, confirma al usuario lo realizado de forma breve. "
    f"{reply_instruction()}"
)


def date_context_line() -> str:
    """Línea de contexto con la fecha/hora real (se inyecta en el mensaje)."""
    try:
        now = datetime.now(ZoneInfo(settings.timezone))
    except Exception:
        now = datetime.now()
    return "[Contexto: hoy es %s (%s)]" % (
        now.strftime("%A %d/%m/%Y %H:%M"),
        settings.timezone,
    )


# Reglas de grounding compartidas por chat y voz (Bloque 1, 2026-09-29):
# el modelo solo puede afirmar lo que devuelvan las herramientas o el usuario.
GROUNDING_RULES = (
    "GROUNDING_RULE (critica): responde SOLO con informacion que provenga de la "
    "salida real de las herramientas o del mensaje del usuario. Si una "
    "herramienta devuelve una lista vacia, un error o no esta disponible, di "
    "exactamente que no hay resultados o que no pudiste comprobarlo; nunca "
    "completes huecos con suposiciones ni ejemplos.\n"
    "TRUTH_RULE (critica): NUNCA inventes datos. No inventes correos, "
    "remitentes, direcciones, fechas, importes, eventos ni tareas. Si una "
    "herramienta no devuelve resultados o falla, di exactamente que no hay "
    "resultados (o que no pudiste comprobarlo); jamas rellenes con "
    "suposiciones. Solo puedes afirmar algo si viene de una herramienta o "
    "del propio mensaje del usuario.\n"
    "TOOL_HONESTY_RULE: no afirmes haber hecho una accion (guardar, crear, "
    "enviar, anadir, completar) si no la ha ejecutado una herramienta en "
    "este turno. Si no puedes ejecutarla, dilo claramente.\n"
    "TASK_RULE: cuando el usuario pida guardar, apuntar o recordar una "
    "tarea ('guarda esta tarea', 'apunta que...', 'recuérdame...', 'para "
    "mañana'), llama SIEMPRE a manage_google_tasks (action=create) con el "
    "titulo completo y, si dio una fecha relativa, en `due` tal cual "
    "('mañana', 'el viernes'). Confirma solo si la herramienta responde "
    "con exito.\n"
    "EMAIL_RULE: para cualquier pregunta sobre correos usa SIEMPRE "
    "search_gmail antes de responder y cita unicamente remitente, asunto "
    "y fecha que devuelva la herramienta. Si no hay resultados, dilo; "
    "nunca inventes un correo ni un remitente. Busca de inmediato con lo "
    "que el usuario haya dicho (nombre, parte del nombre, asunto...): NO "
    "pidas confirmacion ni el nombre completo antes de buscar. Si el "
    "usuario DICTA una direccion ('juan arroba gmail punto com'), "
    "escribela ya como juan@gmail.com (arroba->@, punto->., guion bajo->_); "
    "nunca la uses con la palabra 'arroba' ni vuelvas a pedirla.\n"
    "ACTION_RULE (critica): si el usuario pide una accion o una consulta "
    "que alguna herramienta puede hacer (guardar, apuntar, crear, borrar, "
    "listar, buscar, enviar, consultar el tiempo...), llama a la "
    "herramienta adecuada en este mismo turno. No respondas con texto "
    "diciendo que no puedes, ni pidiendo datos que ya tienes; solo pide un "
    "dato si de verdad falta y es imprescindible.\n"
    "CITE_RULE: cuando uses fragmentos de search_second_brain, termina cada "
    "afirmacion documental con su cita ([S1], [S2]...) tal y como aparecen en "
    "el resultado de la herramienta. No inventes citas ni uses citas que no "
    "hayan aparecido. NO escribas ninguna seccion 'Fuentes': la anade el "
    "sistema. En conversacion de voz, no uses estas marcas de cita.\n"
)


def _commands_line() -> str:
    """Lista compacta de comandos para que el modelo pueda explicarlos."""
    from src.models.schemas import COMMANDS_REGISTRY

    return ", ".join("/%s" % c.command for c in COMMANDS_REGISTRY)


def build_system_prompt(voice: bool = False) -> str:
    """Prompt del sistema con contexto dinámico (auditoría 2026-09-26).

    Añade la fecha/hora actual (evita fechas inventadas) y el estado real de
    Google (evita que el modelo diga que no está conectado cuando sí lo está).
    Con `voice=True` sustituye las reglas de formato de chat por las de voz
    (1-3 frases, sin Markdown/tablas) para el modo llamada.
    """
    try:
        now = datetime.now(ZoneInfo(settings.timezone))
    except Exception:
        now = datetime.now()
    fecha = now.strftime("%A, %d/%m/%Y %H:%M")
    if google_services.is_ready:
        google_state = "conectado (calendario: %s)" % google_services.calendar_id
    else:
        google_state = (
            "no configurado; si el usuario pide calendario/Drive, indica que use "
            "/setup_google o /calendario <correo>"
        )
    contexto = (
        f"CONTEXTO_ACTUAL: hoy es {fecha} (zona horaria {settings.timezone}). "
        "Usa SIEMPRE esta fecha para calcular 'hoy', 'mañana', 'pasado mañana', "
        "'el viernes', etc. Nunca inventes fechas.\n"
        f"GOOGLE_RULE: Google está {google_state}. Las herramientas de Google "
        "(calendario y Drive) están disponibles: NO ofrezcas enlaces de "
        "autorización si ya está conectado; úsalas directamente.\n"
        "COMMANDS_RULE: si el usuario pregunta cómo usar un comando o qué hace "
        "(/setup_google, /evento, /demo...), explícalo con claridad y usa "
        "SIEMPRE el nombre exacto (el comando es /setup_google, nunca '/setup'). "
        "Si Google ya está conectado, no le mandes a configurar nada: usa las "
        "herramientas. Comandos del bot: " + _commands_line() + "\n"
        "ACTION_RULE: si la peticion del usuario esta cubierta por una "
        "herramienta, invocala directamente sin pedir confirmacion ni "
        "preguntar detalles que puedas asumir razonablemente.\n"
        + GROUNDING_RULES
        + "CONTACT_RULE: si el usuario pide el telefono, movil, correo o "
        "direccion de una persona (mama, papa, Ana, un amigo...), usa SIEMPRE "
        "find_contact (Google Contacts) ANTES de responder. NUNCA uses "
        "search_knowledge ni search_second_brain para telefonos ni correos: "
        "find_contact ya busca en tus contactos de Google. Si el usuario dice "
        "'mi madre/padre/hermano...', find_contact lo resuelve por variantes y "
        "alias; si devuelve varios, pregunta cual. Si no lo encuentra, dilo y "
        "pide el nombre exacto con el que lo tiene guardado. Cuando el usuario "
        "diga 'X es mi madre/padre/...', guardalo con remember_fact para "
        "resolverlo la proxima vez.\n"
        "TASK_RULE: si en la conversacion aparece una tarea o un compromiso "
        "con fecha («tengo que renovar el dominio el mes que viene», «te envio "
        "el presupuesto el viernes»), propone crearlo YA con "
        "manage_google_tasks, create_event o create_alert segun corresponda; "
        "si es un compromiso de otra persona hacia ti, crea ademas un "
        "recordatorio de seguimiento para la fecha prometida.\n"
    )
    format_rule = (
        "FORMAT_RULE: cuando el usuario pida una tabla, estadisticas, "
        "comparativas o un listado estructurado (finanzas, actividad, eventos, "
        "contactos...), responde con una **tabla Markdown** real "
        "(| columna | columna | y su fila de separacion |---|---|). Nunca "
        "respondas con descripciones de imagenes ni remitas a graficos: texto "
        "formateado en Markdown. Para datos del gimnasio/actividad usa "
        "fitness_daily_steps y/o el segundo cerebro y presenta la tabla.\n"
    )
    if voice:
        return contexto + VOICE_RULE + SYSTEM_PROMPT_VOICE
    return contexto + format_rule + SYSTEM_PROMPT_VOICE


VOICE_RULE = (
    "VOICE_RULE: esto es una CONVERSACION DE VOZ en tiempo real. Responde en "
    "1-3 frases cortas y naturales, como una persona hablando. PROHIBIDO: "
    "Markdown, tablas, vinetas, emojis, URLs, IDs, listados largos y "
    "tecnicismos. Si el dato es largo, da lo esencial y ofrece el resto por "
    "escrito. Si falta un dato imprescindible, pregunta en una frase. No "
    "repitas palabras ni frases (evita bucles). Termina siempre con una frase "
    "completa.\n"
)


async def _elegir_herramienta_por_texto(
    mensaje_actual: dict[str, Any],
    system_prompt: dict[str, Any],
    candidatas: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pide al modelo (en texto, sin embeddings) la herramienta mas adecuada.

    El ranking por embeddings tiene ruido (~0.02) y en frases cortas falla;
    el modelo, viendo el catalogo con descripciones, elige mejor.
    Devuelve la tool elegida o None.
    """
    catalogo = "\n".join(
        "- %s: %s" % (t["function"]["name"], (t["function"].get("description") or "")[:110])
        for t in candidatas
    )
    try:
        eleccion = await asyncio.wait_for(
            llm.chat(
                messages=[
                    system_prompt,
                    mensaje_actual,
                    {
                        "role": "system",
                        "content": (
                            "Responde SOLO con el nombre de la herramienta mas "
                            "adecuada de esta lista para la peticion del usuario "
                            "(o NINGUNA si no hace falta):\n" + catalogo
                        ),
                    },
                ],
                max_tokens=24,
            ),
            timeout=60.0,
        )
    except Exception:
        return None
    return next(
        (t for t in candidatas if t["function"]["name"] in (eleccion or "")),
        None,
    )


async def _prepare_tool_phase(
    text: str, chat_id: int, voice: bool = False
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Fase comun (Telegram y voz): historial + herramientas + ejecucion.

    Devuelve (messages_for_llm, content, tool_calls, tools_for_call). Si hubo
    herramientas, `messages_for_llm` ya incluye sus resultados para que cada
    interfaz haga su composicion final (chat normal o streaming de voz).

    En voz (chat_id 0) las herramientas que guardan datos (tareas, eventos,
    gastos, CRM) se ejecutan con el chat del administrador, para que lo
    apuntado por llamada aparezca en Telegram y en el briefing.
    """
    tool_chat_id = chat_id
    if voice and chat_id == 0 and settings.admin_ids:
        tool_chat_id = settings.admin_ids[0]
    # Citas [S#]: numeración única por turno (mejora 1).
    citations.reset()
    await db.save_chat_message(chat_id, MessageRole.user.value, text)

    # 12 mensajes (2026-09-30): con 6 se perdia el hilo en conversaciones
    # largas; medido en la GPU, la diferencia de latencia es inapreciable
    # (1,11 s vs 1,12 s con gemma4:12b).
    history = await db.get_chat_history(chat_id, 12)
    trimmed_history = []
    for h in history:
        c = h.get("content", "")
        trimmed_history.append(
            {
                "role": h["role"],
                "content": c[:500] + ("..." if len(c) > 500 else ""),
            }
        )

    messages_for_llm: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(voice=voice)}
    ]
    for msg in trimmed_history:
        messages_for_llm.append({"role": msg["role"], "content": msg["content"]})
    # Refuerzo de fecha en el último mensaje del usuario (los modelos pequeños
    # atienden mejor al final de la conversación que al prompt del sistema).
    for index in range(len(messages_for_llm) - 1, -1, -1):
        if messages_for_llm[index]["role"] == "user":
            messages_for_llm[index]["content"] = "%s %s" % (
                date_context_line(),
                messages_for_llm[index]["content"],
            )
            break
    else:
        # Garantiza que la pregunta actual llegue al modelo aunque el
        # historial no la incluya (o este vacio).
        messages_for_llm.append({"role": "user", "content": "%s %s" % (date_context_line(), text)})

    tools_for_call = await select_tools_semantic(text)

    _t_start = _time.time()
    logger.info(
        "[ORCHESTRATOR] chat_id=%d cid=%s tools=%d history=%d chars=%d",
        chat_id,
        get_correlation_id(),
        len(TOOLS_DEFINITIONS),
        len(trimmed_history),
        len(text),
    )

    try:
        content, tool_calls = await asyncio.wait_for(
            llm.chat_with_tools(
                messages=messages_for_llm,
                tools=tools_for_call,
                max_tokens=512,
            ),
            timeout=600.0,
        )
    except TimeoutError:
        logger.error("[ORCHESTRATOR] timeout for chat_id=%d", chat_id)
        return (
            messages_for_llm,
            "Lo siento, el modelo tardo demasiado en responder. Intenta con un mensaje mas corto.",
            [],
            [],
        )
    except OllamaClientError as e:
        logger.error("[ORCHESTRATOR] Ollama error: %s", e)
        return messages_for_llm, f"Error del modelo: {e}", [], []
    except Exception:
        logger.exception("[ORCHESTRATOR] AI error for chat_id=%d", chat_id)
        return messages_for_llm, "Error interno al procesar la respuesta.", [], []

    _elapsed = _time.time() - _t_start
    metrics.observe("llm_chat_latency", _elapsed)
    logger.info(
        "[ORCHESTRATOR] response in %.1fs content_len=%d tool_calls=%s",
        _elapsed,
        len(content or ""),
        bool(tool_calls),
    )

    # Guardia de honestidad + reintentos. El tool-calling de modelos pequenos
    # es no determinista (el 2026-09-30 el mismo mensaje funcionaba o no segun
    # la ejecucion): si la respuesta afirma una accion sin herramienta (riesgo)
    # o la peticion encaja con una herramienta por similitud (score), se
    # reintenta; y si aun asi no llama, se fuerza esa herramienta con una sola
    # tool ofrecida. Nunca se devuelven datos inventados.
    if not tool_calls:
        risk = _hallucination_risk(content or "", text)
        try:
            # Limite corto: si el embedding se atasca, no se bloquea la respuesta.
            top_tools, top_score = await asyncio.wait_for(best_tools_for_message(text), timeout=8.0)
        except Exception:
            top_tools, top_score = [], 0.0
        top_tool = top_tools[0] if top_tools else None
        probable = top_tool is not None and top_score >= TOOL_RETRY_MIN_SCORE
        if risk or probable:
            logger.warning(
                "[ORCHESTRATOR] sin herramienta (riesgo=%s score=%.3f top=%s); reintentando",
                risk,
                top_score,
                top_tool["function"]["name"] if top_tool else "-",
            )
            messages_for_llm.append(
                {
                    "role": "system",
                    "content": (
                        "AVISO: has respondido como si hubieras hecho una accion o has "
                        "citado datos (correos) sin llamar a ninguna herramienta. Si el "
                        "usuario pidio buscar, guardar, crear, completar o enviar algo, "
                        "llama AHORA a la herramienta adecuada (search_gmail para correos, "
                        "manage_google_tasks para tareas). No inventes datos; si no puedes, "
                        "di claramente que no lo has hecho."
                    ),
                }
            )
            try:
                content_retry, tool_calls_retry = await asyncio.wait_for(
                    llm.chat_with_tools(
                        messages=messages_for_llm,
                        tools=tools_for_call,
                        max_tokens=512,
                    ),
                    timeout=600.0,
                )
                if tool_calls_retry:
                    content, tool_calls = content_retry, tool_calls_retry
                    logger.info(
                        "[ORCHESTRATOR] reintento con herramienta: %s",
                        [tc["function"]["name"] for tc in tool_calls],
                    )
                else:
                    risk_retry = _hallucination_risk(content_retry or "", text)
                    if top_tool is not None and (
                        risk or risk_retry or top_score >= TOOL_FORCE_MIN_SCORE
                    ):
                        # Contexto limpio (system + peticion actual + aviso): el
                        # historial sesga al modelo a seguir respondiendo sin
                        # herramientas (verificado 2026-09-30: en limpio llama
                        # a create_event y con historial no).
                        mensaje_actual = next(
                            (m for m in reversed(messages_for_llm) if m.get("role") == "user"),
                            {"role": "user", "content": text},
                        )
                        confiado = top_score >= TOOL_CONFIDENT_SCORE
                        herramientas_forzadas = [top_tool] if confiado else []
                        nombre_top = top_tool["function"]["name"]
                        if not confiado:
                            # El ranking por embeddings tiene ruido en frases
                            # cortas ('apunta que tengo que...' daba get_alerts
                            # 0.466): que el modelo ELIJA en texto, sin
                            # embeddings, entre las herramientas ofrecidas.
                            elegida = await _elegir_herramienta_por_texto(
                                mensaje_actual, messages_for_llm[0], tools_for_call
                            )
                            if elegida is not None:
                                herramientas_forzadas = [elegida]
                                nombre_top = elegida["function"]["name"]
                            else:
                                # top-3: mas contexto que 1, menos ruido que 10
                                herramientas_forzadas = top_tools or tools_for_call
                        aviso = (
                            "ULTIMO AVISO: DEBES llamar ahora a la herramienta "
                            "'%s' con los datos del usuario. No respondas con "
                            "texto." % nombre_top
                        )
                        mensajes_forzados = [
                            messages_for_llm[0],
                            mensaje_actual,
                            {"role": "system", "content": aviso},
                        ]
                        logger.info(
                            "[ORCHESTRATOR] reintento forzado: %s (tools=%d)",
                            nombre_top,
                            len(herramientas_forzadas),
                        )
                        content_forced, calls_forced = await asyncio.wait_for(
                            llm.chat_with_tools(
                                messages=mensajes_forzados,
                                tools=herramientas_forzadas,
                                max_tokens=512,
                            ),
                            timeout=600.0,
                        )
                        if calls_forced:
                            content, tool_calls = content_forced, calls_forced
                            logger.info(
                                "[ORCHESTRATOR] forzado acepto %s",
                                [tc["function"]["name"] for tc in tool_calls],
                            )
                        elif risk or risk_retry:
                            content = HONEST_FALLBACK
                        elif content_retry:
                            content = content_retry
                    elif risk or risk_retry:
                        # El reintento tampoco uso herramientas: mejor honestidad
                        # que datos falsos.
                        logger.warning(
                            "[ORCHESTRATOR] reintento sin herramienta; respuesta honesta (chat_id=%d)",
                            chat_id,
                        )
                        content = HONEST_FALLBACK
                    elif content_retry:
                        content = content_retry
            except Exception as e:
                logger.warning("[ORCHESTRATOR] reintento de herramienta fallo: %s", e)

    if tool_calls:
        results = []
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            logger.info(
                "[ORCHESTRATOR] Tool call: chat_id=%d tool=%s args=%s",
                chat_id,
                func_name,
                str(args)[:200],
            )
            from src.handlers.chat import execute_tool_measured

            result = await execute_tool_measured(tool_chat_id, func_name, args)
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", func_name),
                    "name": func_name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        # Recuperacion (2026-09-30): si TODAS las herramientas fallaron, el
        # modelo pudo elegir mal o la preseleccion no ofrecio la correcta.
        # Se le pregunta en texto viendo el catalogo COMPLETO y se fuerza una
        # sola vez; si acierta, se usan esos resultados.
        fallidas = []
        for r in results:
            try:
                fallidas.append(json.loads(r["content"]))
            except (json.JSONDecodeError, TypeError):
                fallidas.append({})
        if results and all(r.get("success") is False for r in fallidas):
            from src.handlers.chat_tools import get_tools_with_date_context

            mensaje_actual = next(
                (m for m in reversed(messages_for_llm) if m.get("role") == "user"),
                {"role": "user", "content": text},
            )
            elegida = await _elegir_herramienta_por_texto(
                mensaje_actual, messages_for_llm[0], get_tools_with_date_context()
            )
            # Se permite reintentar la MISMA herramienta: el fallo tipico es
            # de argumentos (gemma manda action vacia o 'complete_task'), y el
            # aviso forzado le obliga a rellenarlos bien.
            if elegida is not None:
                logger.info(
                    "[ORCHESTRATOR] recuperacion: todas fallaron, forzando %s",
                    elegida["function"]["name"],
                )
                aviso = (
                    "La herramienta anterior fallo. DEBES llamar ahora a la "
                    "herramienta '%s' con los datos del usuario. No respondas "
                    "con texto." % elegida["function"]["name"]
                )
                try:
                    content2, calls2 = await asyncio.wait_for(
                        llm.chat_with_tools(
                            messages=[
                                messages_for_llm[0],
                                mensaje_actual,
                                {"role": "system", "content": aviso},
                            ],
                            tools=[elegida],
                            max_tokens=512,
                        ),
                        timeout=600.0,
                    )
                except Exception:
                    calls2 = None
                if calls2:
                    from src.handlers.chat import execute_tool_measured as _exec2

                    nuevos = []
                    exito = False
                    for tc in calls2:
                        fn = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except (json.JSONDecodeError, KeyError):
                            args = {}
                        logger.info(
                            "[ORCHESTRATOR] recuperacion tool: %s args=%s",
                            fn,
                            str(args)[:150],
                        )
                        res = await _exec2(tool_chat_id, fn, args)
                        if isinstance(res, dict) and res.get("success") is not False:
                            exito = True
                        nuevos.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", fn),
                                "name": fn,
                                "content": json.dumps(res, ensure_ascii=False),
                            }
                        )
                    if exito:
                        content, tool_calls, results = content2, calls2, nuevos

        if results:
            messages_for_llm.append(
                {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls,
                }
            )
            messages_for_llm.extend(results)

    return messages_for_llm, content or "", tool_calls or [], tools_for_call


async def generate_response(text: str, chat_id: int) -> str:
    """Generate an AI response with tools and RAG for any interface.

    Offers tools to the LLM in a single call. If the model decides to invoke
    a tool (search_second_brain, save_expense, etc.), a second LLM call is
    made with the tool results. For greetings or small talk with no tool
    invocations, only 1 LLM call is made.
    """
    messages_for_llm, content, tool_calls, tools_for_call = await _prepare_tool_phase(text, chat_id)

    if tool_calls:
        try:
            content, _ = await asyncio.wait_for(
                llm.chat_with_tools(
                    messages=messages_for_llm,
                    tools=tools_for_call,
                    max_tokens=512,
                ),
                timeout=600.0,
            )
        except Exception:
            content = "Consulta completada. Revisa el resultado de las herramientas."
        # Resultados de herramientas (para las guardias deterministas).
        resultados = []
        for m in messages_for_llm:
            if m.get("role") == "tool":
                try:
                    resultados.append(json.loads(m.get("content") or "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
        todas_fallidas = bool(resultados) and all(r.get("success") is False for r in resultados)
        detalles_error = "; ".join(
            str(r.get("message") or r.get("error") or "error")[:140] for r in resultados[:2]
        )
        if not content:
            # Composicion vacia tras usar herramientas (visto 2026-09-30):
            # reintento SIN herramientas para forzar una respuesta de texto.
            try:
                content = await asyncio.wait_for(
                    llm.chat(messages=messages_for_llm, max_tokens=512),
                    timeout=120.0,
                )
            except Exception:
                content = ""
            if not content and todas_fallidas:
                content = "No he podido completar la acción: %s" % detalles_error
            elif not content:
                content = (
                    "He consultado tus datos, pero no he podido redactar la "
                    "respuesta. Intenta reformular la pregunta."
                )
        # Si TODAS las herramientas fallaron, el texto no puede afirmar exito
        # (visto 2026-09-30: manage_google_calendar create fallo por falta de
        # fecha y el modelo respondio "He anadido una cita...").
        if todas_fallidas and _hallucination_risk(content or "", text):
            content = "No he podido completar la acción: %s" % detalles_error
        # Saludo generico o negacion falsa tras herramientas que SI dieron
        # datos (2026-09-30: tras list_google_drive respondio "Hola, soy
        # Rafita..." y "no tengo acceso"). Se reintenta y, si insiste, se usa
        # el mensaje real de la herramienta (determinista).
        desviado = (
            not _es_saludo_solo(text)
            and (_respuesta_desviada(content) or _tool_lista_ignorada(messages_for_llm, content))
            and not todas_fallidas
            and _hay_tool_results(messages_for_llm)
        )
        if desviado:
            logger.warning("[ORCHESTRATOR] saludo/negacion falsa tras herramientas; reintentando")
            aviso = {
                "role": "system",
                "content": (
                    "El usuario pregunto: '%s'. Responde a ESA pregunta usando el "
                    "resultado de la herramienta. PROHIBIDO saludar, presentarte, "
                    "empezar por 'Hola' o decir que no tienes acceso: los datos "
                    "estan en el resultado de la herramienta." % text[:200]
                ),
            }
            try:
                content2 = await asyncio.wait_for(
                    llm.chat(messages=messages_for_llm + [aviso], max_tokens=512),
                    timeout=120.0,
                )
            except Exception:
                content2 = ""
            if (
                content2
                and not _respuesta_desviada(content2)
                and not _tool_lista_ignorada(messages_for_llm, content2)
            ):
                content = content2
            else:
                fallback = _ultimo_mensaje_tool(messages_for_llm)
                if fallback:
                    content = fallback

    # Citas [S#] y sección Fuentes (mejora 1): valida las citas contra las
    # fuentes recuperadas en este turno y añade los enlaces al final.
    if content:
        content = finalize_citations(content, citations.sources(), voice=False)
    citations.clear()

    if content:
        await db.save_chat_message(chat_id, MessageRole.assistant.value, content[:2000])

    return content or "No pude generar una respuesta."


def _chunk_words(text: str):
    """Trocea texto en palabras (para emitir una respuesta ya generada)."""
    words = text.split()
    for i, word in enumerate(words):
        yield word + (" " if i < len(words) - 1 else "")


async def generate_response_stream(text: str, chat_id: int, voice: bool = True):
    """Genera la respuesta emitiendo tokens reales (modo llamada de voz).

    - Con herramientas: la composicion final se hace con `chat_stream_tokens`
      (el primer token llega en cuanto el modelo empieza a escribir, sin
      esperar a la respuesta completa).
    - Sin herramientas: la respuesta ya esta generada; se trocea al vuelo.
    """
    messages_for_llm, content, tool_calls, _tools = await _prepare_tool_phase(
        text, chat_id, voice=voice
    )

    full = ""
    if tool_calls:
        # Se retiene la primera frase antes de emitirla para detectar el
        # saludo generico tras herramientas (bug 2026-09-30); si aparece, se
        # responde con el mensaje real de la herramienta.
        buffer = ""
        validado = False
        try:
            async for token in llm.chat_stream_tokens(
                messages=messages_for_llm,
                max_tokens=180 if voice else 512,
                repeat_penalty=1.15 if voice else None,
            ):
                # En voz se quitan las marcas [S#] al vuelo: el token ya no
                # llega al cliente ni al TTS (mejora 1).
                token_out = strip_citation_marks(token) if voice else token
                if validado:
                    full += token
                    if token_out:
                        yield token_out
                    continue
                buffer += token
                listo = buffer.rstrip().endswith((".", "!", "?", ":")) or len(buffer) >= 60
                if not listo:
                    continue
                if (
                    not _es_saludo_solo(text)
                    and _respuesta_desviada(buffer)
                    and _hay_tool_results(messages_for_llm)
                ):
                    logger.warning(
                        "[ORCHESTRATOR] saludo/negacion falsa en streaming; uso la herramienta"
                    )
                    fallback = _ultimo_mensaje_tool(messages_for_llm) or (
                        "Ahora mismo no puedo responderte a eso."
                    )
                    full = fallback
                    for chunk in _chunk_words(fallback):
                        yield chunk
                    buffer = ""
                    break
                validado = True
                full += buffer
                buffer_out = strip_citation_marks(buffer) if voice else buffer
                if buffer_out:
                    yield buffer_out
                buffer = ""
            if buffer and not validado:
                if (
                    not _es_saludo_solo(text)
                    and _respuesta_desviada(buffer)
                    and _hay_tool_results(messages_for_llm)
                ):
                    fallback = _ultimo_mensaje_tool(messages_for_llm) or (
                        "Ahora mismo no puedo responderte a eso."
                    )
                    full = fallback
                    for chunk in _chunk_words(fallback):
                        yield chunk
                else:
                    full += buffer
                    buffer_out = strip_citation_marks(buffer) if voice else buffer
                    if buffer_out:
                        yield buffer_out
        except Exception as e:
            logger.warning("[ORCHESTRATOR] streaming fallo, uso texto completo: %s", e)
            full = content or "Consulta completada. Revisa el resultado."
            yield full
    elif content:
        full = content
        for chunk in _chunk_words(content):
            yield chunk
    else:
        full = "No pude generar una respuesta."
        yield full

    # Citas [S#] (mejora 1): en voz se quitan las marcas; en texto se añaden
    # las fuentes con enlace.
    if full:
        full = finalize_citations(full, citations.sources(), voice=voice)
    citations.clear()

    if full:
        await db.save_chat_message(chat_id, MessageRole.assistant.value, full[:2000])
