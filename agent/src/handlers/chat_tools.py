"""Tool definitions for the Rafita AVP Telegram bot.

This module contains the OpenAI-compatible tool definitions used by the LLM
to invoke actions like saving expenses, searching the second brain, managing
Google Calendar, and more.

Separated from chat.py for maintainability.
"""

TOOLS_DEFINITIONS = [
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
            "description": "Crea un evento o recordatorio en la base de datos. "
            "Úsalo cuando el usuario mencione una fecha, cita, "
            "reunión o recordatorio.",
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
            "description": "Crea una alerta o notificación para el usuario. "
            "Úsalo cuando el usuario quiera ser notificado "
            "sobre algo importante.",
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
            "description": "Guarda información personal sobre el usuario en la "
            "memoria persistente. Úsalo cuando el usuario comparta "
            "datos personales como su nombre, gustos, preferencias, "
            "cumpleaños, dirección, teléfono, etc. Si la clave ya "
            "existe, se actualiza el valor.",
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
            "description": "Gestiona notas en la bóveda local de Obsidian. "
            "Acciones: create (crea nota nueva), append (añade "
            "contenido al final), read (lee contenido), delete "
            "(elimina). Úsalo cuando el usuario diga cosas como "
            "'apunta esto', 'guarda una nota', 'lee la nota de...', "
            "'borra la nota...', 'crea una nota en la carpeta...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "append", "read", "delete"],
                        "description": "Acción a realizar: create, append, read o delete",
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
                        "(ej: 00-Inbox/nota_vieja.md o "
                        "Attachments/foto.jpg)",
                    },
                    "dest_folder": {
                        "type": "string",
                        "description": "Carpeta destino (ej: 01-Proyectos, "
                        "02-Areas/Finanzas, 03-Recursos, "
                        "04-Archivo, Attachments)",
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
            "Usalo cuando el usuario pregunte por cualquier informacion "
            "personal, notas, apuntes, documentos locales, proyectos, "
            "finanzas, o conocimiento almacenado en su vault. "
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
            "description": "Busca en TU segundo cerebro personal (vault de Obsidian) "
            "usando busqueda semantica por embeddings con soporte de "
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
                        "description": "Fecha y hora ISO8601 (ej: 2026-06-15T09:00:00). "
                        "Requerido para create.",
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
            "description": "Crea un evento directamente en el Google Calendar real del usuario. "
            "Usalo cuando el usuario pida agendar algo en su calendario de Google, "
            "como 'agenda una reunion manana a las 5', 'anade un evento a mi "
            "calendario'. Despues de crear el evento, sincroniza en Obsidian.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Titulo del evento",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "Fecha y hora ISO8601 (ej: 2026-06-25T17:00:00)",
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
                "required": ["title", "start_datetime"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_file",
            "description": "Registra la existencia de un archivo en el segundo cerebro "
            "creando una nota companion con metadatos. Usalo despues de "
            "que el usuario mencione o comparta un archivo (PDF, DOCX, TXT, CSV) "
            "para que quede indexado en el vault y sea buscable semanticamente. "
            "Incluye el nombre del archivo, carpeta donde se guardo, tipo de "
            "contenido y etiquetas para clasificarlo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nombre del archivo incluyendo extension",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Carpeta destino en el vault (ej: 03-Recursos, "
                        "02-Areas/Finanzas, 01-Proyectos)",
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
