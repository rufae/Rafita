"""Tool definitions for the Rafita AVP Telegram bot.

This module contains the OpenAI-compatible tool definitions used by the LLM
to invoke actions like saving expenses, searching the second brain, managing
Google Calendar, and more.

Separated from chat.py for maintainability.
"""

from typing import Any

from src.vault_config import get_taxonomy

# Tools that write to the second brain. With PERSIST_TO_BRAIN=false they are
# rejected at the executor level (task 2.3), not only hidden from the prompt.
WRITE_TOOLS = {"manage_obsidian_note", "move_or_rename_file", "ingest_file"}

TOOLS_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_expense",
            "description": "Registra un gasto o egreso financiero en la base de datos. "
            "Úsalo cuando el usuario mencione que pagó, gastó, compró o "
            "desembolsó dinero.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Monto del gasto en número (ej: 150.50)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Categoría del gasto (ej: alimentacion, "
                        "transporte, servicios, entretenimiento, "
                        "salud, educacion, otros)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Descripción opcional del gasto",
                    },
                },
                "required": ["amount", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Crea un evento o cita CON fecha y hora en la agenda local del "
            "asistente (base de datos propia). Usalo cuando el usuario mencione "
            "una fecha, cita, reunion o evento con hora. Si solo pide un aviso "
            "o recordatorio sin fecha/hora, usa create_alert. Si menciona "
            "Google Calendar, usa create_google_calendar_event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título del evento",
                    },
                    "event_datetime": {
                        "type": "string",
                        "description": "Fecha y hora del evento en formato YYYY-MM-DD HH:MM",
                    },
                    "description": {
                        "type": "string",
                        "description": "Descripción opcional del evento",
                    },
                },
                "required": ["title", "event_datetime"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_alert",
            "description": "Crea una alerta o aviso importante del asistente (no es Google "
            "Tasks). Usalo para 'recuérdame...', 'avísame...', 'es urgente' o "
            "'es importante' cuando NO haya una cita con fecha y hora concreta "
            "(para una cita con fecha/hora usa create_event). Para tareas de "
            "Google usa manage_google_tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Mensaje de la alerta",
                    },
                    "alert_type": {
                        "type": "string",
                        "enum": ["info", "warning", "urgent"],
                        "description": "Tipo de alerta: info (informativa), "
                        "warning (advertencia), urgent (urgente)",
                    },
                    "expires_at": {
                        "type": "string",
                        "description": "Fecha de expiración en formato YYYY-MM-DD (opcional)",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_finance_summary",
            "description": "Obtiene un resumen financiero del mes actual. "
            "Úsalo cuando el usuario pregunte por su situación "
            "financiera, balance, ingresos o gastos.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Guarda informacion personal sobre el usuario en la memoria "
            "persistente. Usalo SIEMPRE que el usuario comparta un dato "
            "personal: nombre, gustos, preferencias, cumpleanos, direccion, "
            "telefono, tallas (ropa, zapato), alergias, familia, mascotas, "
            "horarios, numeros de cuenta... aunque lo diga de pasada. Si la "
            "clave ya existe, se actualiza el valor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Identificador breve de la información "
                        "(ej: nombre_completo, direccion, telefono, "
                        "gusto_musical, alergias)",
                    },
                    "value": {
                        "type": "string",
                        "description": "Valor o contenido de la información",
                    },
                    "category": {
                        "type": "string",
                        "description": "Categoría opcional: personal, contacto, "
                        "salud, gustos, trabajo, otros",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Busca información personal almacenada del usuario "
            "en la memoria persistente. Úsalo cuando el usuario "
            "pregunte '¿qué sabes de mí?', o cuando necesites "
            "recordar datos personales para dar contexto a la "
            "conversación. Devuelve todos los hechos relevantes "
            "a la consulta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda (ej: nombre, "
                        "dirección, teléfono, cumpleaños, gustos)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Busca información actualizada en internet. Úsalo "
            "cuando el usuario pregunte por noticias, información "
            "reciente, precios, datos que no conoces, o cualquier "
            "cosa que requiera consultar la web. Devuelve resúmenes "
            "de las páginas encontradas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Consulta de búsqueda en internet",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_obsidian_note",
            "description": "Gestiona notas en la bóveda local de Obsidian. Invocala "
            "directamente (sin preguntar) cuando el usuario pida guardar, "
            "apuntar, crear, leer, editar o borrar una nota. "
            "Acciones: create (crea nota nueva), append (añade "
            "contenido al final), overwrite (reemplaza todo el "
            "contenido), read (lee contenido), delete (elimina). "
            "Úsalo cuando el usuario diga cosas como 'apunta esto', "
            "'guarda una nota', 'guarda este correo como nota', "
            "'lee la nota de...', 'edita/reemplaza la nota...', "
            "'borra la nota...', 'crea una nota en la carpeta...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "append", "overwrite", "read", "delete"],
                        "description": "Acción: create, append, overwrite, read o delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "Título de la nota (sin extensión .md)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenido en Markdown (requerido para create y append)",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Subcarpeta dentro de la bóveda (ej: Ideas, "
                        "Reuniones, Proyectos). Vacío para raíz.",
                    },
                },
                "required": ["action", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_obsidian_vault",
            "description": "Busca palabras clave dentro de todas las notas de "
            "la bóveda Obsidian. Úsalo cuando el usuario pregunte "
            "'búscame en Obsidian', 'qué escribí sobre...', "
            "'encuentra la nota donde hablo de...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Palabra clave o frase a buscar en las notas",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_project_files",
            "description": "Lista archivos y directorios dentro del proyecto "
            "RafAI (/workspace). Úsalo cuando el usuario pregunte "
            "'qué archivos hay en...', 'muéstrame el proyecto', "
            "'explora la carpeta...', o quiera inspeccionar la "
            "estructura del código.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta relativa dentro del proyecto (ej: "
                        "'agent/src', 'docker-compose.yml', "
                        "vacío para raíz)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_system_logs",
            "description": "Analiza el estado de salud del sistema: tamaño de "
            "BD, espacio en disco, errores recientes en logs, y "
            "chats activos. Úsalo cuando el usuario pregunte "
            "'cómo estás funcionando', 'ha habido errores', "
            "'muéstrame el estado del sistema', 'estado de salud'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_or_rename_file",
            "description": "Mueve o renombra un archivo dentro de la boveda "
            "de Obsidian. Úsalo cuando el usuario diga cosas "
            "como 'mueve la nota X a la carpeta Y', 'renombra "
            "el archivo Z como...', 'pasa el archivo de Inbox "
            "a Proyectos', 'reorganiza mi nota de...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Ruta actual dentro de la boveda "
                        f"(ej: {get_taxonomy().path('inbox')}/nota_vieja.md o "
                        f"{get_taxonomy().path('attachments')}/foto.jpg)",
                    },
                    "dest_folder": {
                        "type": "string",
                        "description": "Carpeta destino (ej: "
                        f"{get_taxonomy().path('projects')}, "
                        f"{get_taxonomy().path('areas_finanzas')}, "
                        f"{get_taxonomy().path('resources')}, "
                        f"{get_taxonomy().path('archive')}, "
                        f"{get_taxonomy().path('attachments')})",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Nuevo nombre sin extension "
                        "(ej: Ideas_2026, Factura_Luz_Enero)",
                    },
                },
                "required": ["source_path", "dest_folder"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_deep_knowledge_base",
            "description": "Busca informacion en el segundo cerebro (vault de Obsidian + "
            "documentos indexados) usando busqueda semantica por embeddings. "
            "Usalo SIEMPRE que la pregunta trate de informacion personal, notas, "
            "apuntes, diario, proyectos, finanzas o documentos locales, aunque el "
            "usuario diga solo 'documento' o 'busca'. NO es Google Drive. "
            "Este es un sistema RAG local que entiende "
            "el significado, no solo palabras clave.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Pregunta o consulta sobre el contenido "
                        "de los documentos y notas personales",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Numero de fragmentos a recuperar (default 5, max 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_second_brain",
            "description": "Busca en TU segundo cerebro personal (vault de Obsidian, NO "
            "Google Drive) usando busqueda semantica por embeddings con soporte de "
            "filtro por etiquetas. Devuelve fragmentos relevantes con "
            "la ruta exacta de la nota origen, el encabezado donde "
            "aparece, y un enlace obsidian:// para abrirla directamente. "
            "USALO SIEMPRE que el usuario pregunte sobre cualquier cosa "
            "que pueda estar en sus notas personales: proyectos, finanzas, "
            "ideas, apuntes tecnicos, diario, recursos. "
            "Tambien usalo para preguntas tipo 'que sabes de...', "
            "'que tengo sobre...', 'que escribi acerca de...', "
            "'busca en mis notas...'. "
            "Siempre cita la nota origen (note_path) en tu respuesta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Pregunta o consulta en lenguaje natural "
                        "sobre el contenido de las notas personales",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas para filtrar (ej: [finanzas, misterai]). "
                        "Vacio para buscar en todo el vault",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Numero de fragmentos a recuperar (default 6, max 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_google_calendar",
            "description": "Gestiona eventos en Google Calendar real del usuario. "
            "Acciones: create (crea evento nuevo), list (lista "
            "proximos eventos), delete (elimina por ID). "
            "Usalo cuando el usuario pida anadir un evento a "
            "su calendario, agendar una cita, reunion, "
            "o consultar su agenda de Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "delete"],
                        "description": "Accion: create, list o delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titulo del evento (requerido para create)",
                    },
                    "datetime_str": {
                        "type": "string",
                        "description": "Fecha y hora ISO8601 SOLO si el usuario la dio en "
                        "absoluto (ej: 2026-06-15T09:00:00). Si dio una fecha relativa "
                        "('mañana a las 10', 'el viernes'), deja esto vacio y usa 'when'.",
                    },
                    "when": {
                        "type": "string",
                        "description": "Frase temporal tal cual la dijo el usuario (ej: "
                        "'mañana a las 10', 'el viernes', 'en 3 dias'). El servidor la "
                        "convierte a fecha real. Usalo siempre que sea una fecha relativa.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Descripcion opcional del evento",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "ID del evento en Google Calendar (requerido para delete)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_recurring_reminder",
            "description": "Configura un recordatorio recurrente con patron "
            "temporal. Patrones soportados: daily (cada 24h), "
            "weekly (cada 7 dias), every_X_hours (cada X horas, "
            "ej: every_2_hours), weekdays (lunes a viernes), "
            "weekends (sabado y domingo). "
            "Usalo cuando el usuario pida recordatorios como "
            "'recuerdame cada dia', 'cada semana', "
            "'cada 3 horas', 'solo en dias laborables'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Patron recurrente: daily, weekly, "
                        "every_X_hours, weekdays, weekends",
                    },
                    "message": {
                        "type": "string",
                        "description": "Mensaje del recordatorio",
                    },
                    "time_str": {
                        "type": "string",
                        "description": "Hora opcional para el primer aviso "
                        "en formato HH:MM (ej: 09:00). "
                        "Si se omite, comienza ahora.",
                    },
                },
                "required": ["pattern", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_google_auth_link",
            "description": "Genera el enlace de autorizacion de Google OAuth2 para que "
            "el usuario conecte su cuenta de Google (Calendar, Tasks, Drive). "
            "Usalo cuando el usuario pida acceder a su calendario de Google "
            "y no este autenticado, o cuando te devuelva un error de "
            "'No autenticado'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_google_verification_code",
            "description": "Recibe el codigo de verificacion que Google le da al usuario "
            "tras autorizar la aplicacion en el navegador. Intercambia el "
            "codigo por credenciales de acceso permanentes. Usalo cuando el "
            "usuario te pegue un codigo de Google despues de visitar el "
            "enlace de autorizacion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "auth_code": {
                        "type": "string",
                        "description": "Codigo de verificacion que Google muestra al usuario "
                        "tras autorizar la app",
                    },
                },
                "required": ["auth_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_google_calendar_events",
            "description": "Consulta los proximos eventos del Google Calendar del usuario. "
            "Usalo cuando el usuario pregunte 'que tengo manana', 'mi agenda', "
            "'eventos de la semana', 'que hay en mi calendario'. "
            "Si devuelve error de no autenticado, usa generate_google_auth_link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Numero maximo de eventos a traer (default 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_google_calendar_event",
            "description": "Crea un evento en Google Calendar. Usalo SOLO cuando el usuario "
            "mencione Google Calendar o 'mi calendario de Google'. Si dice "
            "'agendame', 'apuntame una cita' o similar SIN mencionar Google, usa "
            "create_event (agenda local). Despues de crear el evento, sincroniza "
            "en Obsidian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Titulo del evento",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "Fecha y hora ISO8601 SOLO si el usuario la dio en "
                        "absoluto. Si dio una fecha relativa ('mañana a las 10', 'el "
                        "viernes'), deja esto vacio y usa 'when'.",
                    },
                    "when": {
                        "type": "string",
                        "description": "Frase temporal tal cual la dijo el usuario (ej: "
                        "'mañana a las 10', 'el viernes', 'en 3 dias'). El servidor la "
                        "convierte a la fecha real de hoy. Usalo siempre que sea relativa.",
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "Fecha y hora de fin ISO8601 (opcional)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Descripcion opcional del evento",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_google_drive",
            "description": "Busca ficheros en Google Drive (la nube de Google) por nombre o "
            "texto contenido. Usalo SOLO si el usuario menciona Google Drive, 'mi "
            "drive', 'la nube de Google' o un fichero que subio alli. Para LISTAR "
            "lo que tiene en Drive ('que carpetas tengo', 'que archivos hay') usa "
            "list_google_drive. NO lo uses para notas personales, apuntes, diario "
            "o el vault: para eso usa search_second_brain, ask_deep_knowledge_base "
            "o search_obsidian_vault. Devuelve nombre e id; para leer el contenido "
            "usa read_google_drive_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto a buscar (nombre o contenido del fichero)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximo de resultados (1-25, por defecto 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_google_drive_file",
            "description": "Lee el texto de un fichero de Google Drive (Google Docs, Sheets, "
            "PDF o texto plano) a partir del id devuelto por search_google_drive. "
            "Usalo cuando el usuario pida 'leeme el documento', 'resume el archivo', "
            "'que dice el fichero...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "Id del fichero devuelto por search_google_drive",
                    },
                },
                "required": ["file_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_gmail",
            "description": "Busca correos en Gmail del usuario (solo lectura) y devuelve asunto, "
            "remitente, fecha y un extracto. Usalo para 'resumeme los correos de hoy', "
            "'busca el correo de...', 'que me ha mandado...'. Admite consultas Gmail: "
            "'is:unread', 'from:banco', 'newer_than:1d', 'subject:factura'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Consulta Gmail (is:unread, from:, newer_than:1d, subject:...)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximo de correos (1-10, por defecto 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_google_tasks",
            "description": "Gestiona las tareas de Google Tasks del usuario: list (listar), "
            "create (crear con titulo), complete (marcar hecha por task_id), delete "
            "(borrar por task_id). Usalo para 'anademe una tarea', 'que tareas tengo', "
            "'marca como hecha la tarea...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "complete", "delete"],
                    },
                    "title": {
                        "type": "string",
                        "description": "Titulo de la tarea (para create)",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "ID de la tarea (para complete/delete)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_contact",
            "description": "Busca un contacto en Google Contacts por nombre, correo o telefono. "
            "Usalo para 'dame el telefono de...', 'cual es el correo de...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Nombre, correo o telefono a buscar",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fitness_daily_steps",
            "description": "Devuelve los pasos que el usuario lleva hoy (Google Fit). "
            "Usalo para 'cuantos pasos llevo hoy', 'como voy de actividad'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_google_drive",
            "description": "Lista el contenido de Google Drive: carpetas, archivos o todo. "
            "Usalo cuando el usuario pregunte 'que tengo en mi drive', 'que "
            "carpetas tengo', 'ensename mis archivos de drive'. NO mezcles: si "
            "pide carpetas, kind=folders; si pide archivos, kind=files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["all", "folders", "files"],
                        "description": "Que listar: all (todo), folders o files",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximo de elementos (1-50, por defecto 20)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_file",
            "description": "Registra en el segundo cerebro (vault) un archivo que el usuario "
            "ha subido o compartido (PDF, DOCX, TXT, CSV), creando una nota con "
            "sus metadatos para que quede indexado. Usalo cuando diga 'he subido "
            "un PDF', 'te he pasado un documento', 'guarda este archivo en el "
            "segundo cerebro'. NO es Google Drive: no busques en Drive cuando la "
            "intencion sea registrar el archivo en la boveda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nombre del archivo incluyendo extension",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Carpeta destino en el vault (ej: "
                        f"{get_taxonomy().path('resources')}, "
                        f"{get_taxonomy().path('areas_finanzas')}, "
                        f"{get_taxonomy().path('projects')})",
                    },
                    "note_type": {
                        "type": "string",
                        "description": "Tipo de nota: recurso, proyecto, area, nota-atomica",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas para clasificar el archivo",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Resumen del contenido del archivo en una linea",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenido textual extraido del archivo",
                    },
                },
                "required": ["filename", "folder", "note_type"],
            },
        },
    },
]


_TOOL_EMBEDDINGS: dict[str, list[float]] | None = None


def _tool_catalog_text(tool: dict[str, Any]) -> str:
    return "%s: %s" % (tool["function"]["name"], tool["function"]["description"])


def rank_tools_by_similarity(
    message_vec: list[float], tool_vecs: dict[str, list[float]], tools: list[dict[str, Any]], k: int
) -> list[dict[str, Any]]:
    """Ranking puro por similitud (testeable sin red). bge-m3 da vectores
    unitarios, así que el producto escalar es la similitud coseno."""

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    ranked = sorted(
        tools,
        key=lambda t: dot(message_vec, tool_vecs.get(t["function"]["name"], [])),
        reverse=True,
    )
    return ranked[: max(1, min(k, len(ranked)))]


async def select_tools_semantic(text: str, k: int = 10) -> list[dict[str, Any]]:
    """Preselecciona herramientas por similitud semántica (no por palabras).

    El usuario puede pedir lo mismo de mil formas: se embebe su mensaje y las
    descripciones de las herramientas (bge-m3) y se ofrecen las `k` más
    parecidas; **el modelo decide** cuál usar entre ellas. Si el embedding
    falla, se ofrecen todas (comportamiento previo).
    """
    import asyncio

    from src.ollama_client import llm

    tools = get_tools_with_date_context()
    global _TOOL_EMBEDDINGS
    try:
        if _TOOL_EMBEDDINGS is None or len(_TOOL_EMBEDDINGS) != len(tools):
            vectors = await asyncio.to_thread(
                llm.embed_texts, [_tool_catalog_text(t) for t in tools]
            )
            _TOOL_EMBEDDINGS = {
                t["function"]["name"]: v for t, v in zip(tools, vectors, strict=False)
            }
        message_vec = (await asyncio.to_thread(llm.embed_texts, [text]))[0]
    except Exception:
        return tools
    return rank_tools_by_similarity(message_vec, _TOOL_EMBEDDINGS, tools, k)


def get_tools_for_message(text: str) -> list[dict[str, Any]]:
    """Compatibilidad: devuelve todas las herramientas (sin filtrar).

    La selección real es `select_tools_semantic` (embeddings + decisión del
    modelo); se probó el filtrado por palabras clave y es frágil.
    """
    return get_tools_with_date_context()


def get_tools_with_date_context() -> list[dict[str, Any]]:
    """Copia de todas las herramientas (sin sello de fecha en descripciones).

    Nota: se probó a inyectar "[HOY es ...]" en la descripción de las
    herramientas de calendario y **degradaba** la selección del modelo. La
    fecha va en el prompt del sistema, en el último mensaje del usuario y en
    el parámetro `when` + parser determinista.
    """
    import copy

    return copy.deepcopy(get_tools_for_llm())


def get_tools_for_llm() -> list[dict[str, Any]]:
    """Tool definitions offered to the LLM.

    In debug mode (PERSIST_TO_BRAIN=false) write tools are not even offered.
    The executor still rejects them, so a model that calls them anyway cannot
    modify the vault.
    """
    from src.config import settings

    if settings.persist_to_brain:
        return TOOLS_DEFINITIONS
    return [tool for tool in TOOLS_DEFINITIONS if tool["function"]["name"] not in WRITE_TOOLS]
