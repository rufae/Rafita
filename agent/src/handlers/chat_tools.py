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
            "description": "Crea una cita, evento o reunion CON fecha y hora en la agenda "
            "(ej: cita el viernes a las 10, reunion manana a las 9). Para "
            "tareas, recados o cosas que tengo que hacer sin hora concreta usa "
            "manage_google_tasks; para avisos sin fecha usa create_alert; si "
            "menciona Google Calendar usa create_google_calendar_event.",
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
            "name": "get_weather",
            "description": "Consulta el tiempo (hoy o mañana) para la ciudad del "
            "usuario o para otra ciudad. Usalo cuando pregunte por el tiempo, "
            "la temperatura, la lluvia o la prevision meteorologica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ciudad": {
                        "type": "string",
                        "description": "Ciudad opcional (ej: Sevilla). Si no se indica, "
                        "se usa la del usuario.",
                    },
                    "dia": {
                        "type": "string",
                        "enum": ["hoy", "mañana"],
                        "description": "Dia de la prediccion (por defecto hoy)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "Lista las alertas y recordatorios pendientes del usuario "
            "(con su ID). Usalo cuando pregunte '¿qué alertas tengo?', "
            "'¿qué recordatorios tengo?' o similar.",
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
            "'muéstrame el estado del sistema', 'estado de salud'. "
            "NO es para consultar ejecuciones de automatizaciones o "
            "flujos n8n: para eso está get_automation_runs.",
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
            "filtro por etiquetas y reranking lexico (ideal para nombres propios). "
            "Devuelve fragmentos numerados [S1], [S2]... con la ruta exacta de la "
            "nota origen, el encabezado donde aparece y un enlace obsidian:// para "
            "abrirla directamente. Cada afirmacion que hagas con estos fragmentos "
            "debe terminar con su cita ([S1], [S2]...). "
            "USALO SIEMPRE que el usuario pregunte sobre cualquier cosa "
            "que pueda estar en sus notas personales: proyectos, finanzas, "
            "ideas, apuntes tecnicos, diario, recursos. "
            "Tambien usalo para preguntas tipo 'que sabes de...', "
            "'que tengo sobre...', 'que escribi acerca de...', "
            "'busca en mis notas...'.",
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
            "name": "add_relation",
            "description": "Guarda una relacion tipada en el grafo de conocimiento del "
            "usuario (sujeto — predicado — objeto). Usalo cuando el usuario diga "
            "cosas como 'apunta que Ana trabaja en el proyecto X', 'mi equipo usa "
            "Rafita para las reuniones' o cualquier hecho relacional que quiera "
            "recordar (personas, proyectos, tecnologias, lugares).",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Sujeto de la relacion (ej: 'Ana', 'Proyecto X')",
                    },
                    "predicate": {
                        "type": "string",
                        "description": "Relacion en minusculas (ej: 'trabaja_en', 'usa', "
                        "'vive_en', 'participo_en', 'es_responsable_de')",
                    },
                    "object": {
                        "type": "string",
                        "description": "Objeto de la relacion (ej: 'Proyecto X', 'Rafita')",
                    },
                    "source": {
                        "type": "string",
                        "description": "Origen del dato (por defecto 'conversacion')",
                    },
                },
                "required": ["subject", "predicate", "object"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_relations",
            "description": "Consulta el grafo de conocimiento: relaciones guardadas entre "
            "personas, proyectos, tecnologias o lugares. Usalo para preguntas como "
            "'quien trabaja en el proyecto X', 'que sabemos de Ana', 'en que "
            "participo Juan' o 'que relaciones hay sobre Rafita'. Sin argumentos "
            "lista todas las relaciones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto a buscar en sujeto, predicado u objeto "
                        "(vacio para listar todas)",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Filtrar por sujeto exacto (opcional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_relation",
            "description": "Borra una relacion del grafo de conocimiento por su id. "
            "Usalo cuando el usuario pida eliminar un hecho guardado (obten el id "
            "antes con search_relations).",
            "parameters": {
                "type": "object",
                "properties": {
                    "relation_id": {
                        "type": "integer",
                        "description": "Id numerico de la relacion a borrar",
                    },
                },
                "required": ["relation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_my_data",
            "description": "Genera una exportacion RGPD con TODOS los datos del usuario "
            "(historial, conocimiento personal, eventos, finanzas, relaciones, "
            "reuniones...) en un fichero JSON y devuelve su ruta. Usalo cuando el "
            "usuario pida exportar o descargar sus datos personales.",
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
            "name": "delete_my_data",
            "description": "BORRA de forma irreversible todos los datos del usuario "
            "(historial de chat, conocimiento personal, eventos, alertas, finanzas, "
            "relaciones, reuniones y sesiones de voz). REQUIERE confirm=true; si el "
            "usuario no lo ha confirmado explicitamente, primero explicale que se "
            "borra TODO y pide confirmacion. Usalo solo ante una peticion explicita "
            "de borrado de datos (derecho de supresion RGPD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "true SOLO si el usuario ha confirmado explicitamente "
                        "que quiere borrar todos sus datos",
                    },
                },
                "required": ["confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_google_calendar",
            "description": "Borra, elimina, cancela, mueve, lista o crea eventos y "
            "citas en el calendario del usuario (Google Calendar o local). "
            "Acciones: create (crear), list (listar), delete (borrar/eliminar "
            "un evento por titulo o ID), move (cambiar fecha/hora). Usalo "
            "cuando diga borra el evento, elimina la cita, mueve la reunion o "
            "apunta una cita.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "delete", "move"],
                        "description": "Accion: create, list o delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titulo del evento (create; o delete si no tienes event_id)",
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
            "name": "manage_crm",
            "description": "Gestiona el mini-CRM de clientes (notas en la carpeta CRM/ "
            "de la boveda). Acciones: create/update (crea o actualiza un "
            "cliente con sus datos), note (anade una nota fechada), estado "
            "(cambia el estado: lead, contactado, propuesta, cerrado, "
            "perdido), contacto (registra que hubo contacto hoy), list "
            "(lista clientes, opcionalmente por estado) y summary (resumen "
            "del pipeline con totales). Usalo con frases como 'mi cliente "
            "Maria', 'pipeline', 'marca a Maria como cerrado', 'anota que "
            "Juan quiere presupuesto'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "update",
                            "note",
                            "estado",
                            "contacto",
                            "list",
                            "summary",
                        ],
                        "description": "Accion a realizar",
                    },
                    "nombre": {
                        "type": "string",
                        "description": "Nombre del cliente (requerido salvo en list/summary)",
                    },
                    "estado": {
                        "type": "string",
                        "enum": ["lead", "contactado", "propuesta", "cerrado", "perdido"],
                        "description": "Estado del cliente",
                    },
                    "valor": {
                        "type": "number",
                        "description": "Valor economico del cliente/proyecto",
                    },
                    "email": {"type": "string", "description": "Email de contacto"},
                    "telefono": {"type": "string", "description": "Telefono de contacto"},
                    "empresa": {"type": "string", "description": "Empresa del cliente"},
                    "proximo_paso": {
                        "type": "string",
                        "description": "Siguiente paso acordado (ej: 'enviar presupuesto')",
                    },
                    "proximo_seguimiento": {
                        "type": "string",
                        "description": "Fecha de seguimiento AAAA-MM-DD (si el usuario la dio)",
                    },
                    "nota": {
                        "type": "string",
                        "description": "Texto de la nota (acciones create/update/note)",
                    },
                    "fecha": {
                        "type": "string",
                        "description": "Fecha del contacto AAAA-MM-DD (accion contacto; por defecto hoy)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_sequences",
            "description": "Gestiona secuencias de email automaticas para un contacto "
            "(seguimiento de propuestas, onboarding...): create (crea la "
            "secuencia con pasos escalonados; los emails los redacta Rafita "
            "con el contexto), list (lista secuencias), stop/resume (pausar o "
            "reactivar), run (ejecuta ahora los envios vencidos). La secuencia "
            "se detiene sola si el contacto responde. Usalo con 'crea una "
            "secuencia para Maria (propuesta web)', 'como van las "
            "secuencias', 'para la secuencia de Juan'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "stop", "resume", "run"],
                    },
                    "nombre": {
                        "type": "string",
                        "description": "Nombre de la secuencia (p. ej. 'Propuesta web Maria')",
                    },
                    "contacto": {
                        "type": "string",
                        "description": "Nombre del contacto o cliente",
                    },
                    "email": {
                        "type": "string",
                        "description": "Email del contacto",
                    },
                    "contexto": {
                        "type": "string",
                        "description": "Contexto para personalizar los emails (proyecto, "
                        "propuesta, ultima conversacion...)",
                    },
                    "pasos": {
                        "type": "string",
                        "description": "Opcional: pasos 'dias:objetivo' separados por comas "
                        "(ej: '0:presentacion,3:seguimiento,7:ultimo aviso'). Si no "
                        "se indica, se usan 0/3/7 dias.",
                    },
                    "sequence_id": {
                        "type": "number",
                        "description": "ID de la secuencia (stop/resume)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Solo redactar sin enviar (action=run)",
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
            "description": "Consulta los proximos eventos de la agenda del usuario "
            "(Google Calendar y, si no esta conectado, la agenda local). "
            "Usalo cuando el usuario pregunte 'que tengo manana', 'mi agenda', "
            "'eventos de la semana', 'que hay en mi calendario'. Para un periodo "
            "concreto usa days (p. ej. days=7 para 'la proxima semana'). "
            "Si el usuario pregunta como configurar Google, dile que use "
            "/setup_google (no '/setup') y explicale el paso.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Numero maximo de eventos a traer (default 10)",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Solo eventos de los proximos N dias (p. ej. 7 = "
                        "proxima semana). Vacio = todos los proximos.",
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
            "description": "Anade un evento NUEVO a Google Calendar (crear). Usalo SOLO "
            "cuando el usuario mencione Google Calendar para CREAR algo. Para "
            "borrar o mover usa manage_google_calendar; para la agenda local usa "
            "create_event.",
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
            "name": "send_gmail",
            "description": "Envia un correo electronico desde la cuenta de Gmail del usuario. "
            "Usalo SIEMPRE que pida mandar/enviar un correo o email a alguien "
            "('manda un correo a mama diciendole que la quiero', 'enviale un "
            "email a juan', 'escribe a... diciendo...'): el destinatario puede "
            "ser un correo o solo un nombre (se busca solo en tus contactos). "
            "Si solo pide REDACTAR o preparar un correo sin enviarlo, usa "
            "draft_gmail en su lugar. No uses find_contact para esto: send_gmail "
            "ya resuelve el nombre.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Correo del destinatario o su nombre (se resuelve solo)",
                    },
                    "subject": {"type": "string", "description": "Asunto del correo"},
                    "body": {"type": "string", "description": "Cuerpo del correo"},
                },
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_gmail",
            "description": "REDACTA un correo en Gmail como borrador, SIN enviarlo. "
            "Usalo cuando pida redactar, preparar, escribir un borrador o "
            "componer un correo ('redactale un correo a X', 'prepara un email "
            "para...'): el correo queda en Gmail para revisarlo y enviarlo "
            "despues. Si lo que pide es ENVIAR ya, usa send_gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Correo del destinatario o su nombre (se resuelve solo)",
                    },
                    "subject": {"type": "string", "description": "Asunto del correo"},
                    "body": {"type": "string", "description": "Cuerpo del correo"},
                },
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_n8n",
            "description": "Ejecuta una automatizacion (workflow) de n8n del usuario. "
            "Usalo cuando diga 'ejecuta la automatizacion de X', 'lanza el flujo "
            "de facturas', 'mándale el informe con tu flujo'. El nombre debe ser "
            "uno de los definidos por el usuario (p. ej. facturas, informes) o "
            "una URL de webhook de n8n.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Nombre de la automatizacion (ej: facturas) o URL del webhook",
                    },
                    "payload": {
                        "type": "object",
                        "description": "Datos opcionales que el flujo necesite",
                    },
                },
                "required": ["workflow"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_google_tasks",
            "description": "Gestiona las tareas pendientes del usuario (Google Tasks si esta "
            "conectado; si no, locales): list, create (con titulo y fecha en "
            "due), complete y delete (por task_id o title/task_title). Usalo "
            "cuando pida guardar, apuntar o recordar una TAREA o recado: "
            "apunta que tengo que comprar pilas, recuerdame llamar a mama, "
            "guarda esta tarea, que tareas tengo, borra la tarea X. NO lo uses "
            "para citas, reuniones o eventos con fecha y hora (eso es "
            "create_event o manage_google_calendar).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "complete", "delete"],
                    },
                    "title": {
                        "type": "string",
                        "description": "Titulo completo de la tarea (para create)",
                    },
                    "due": {
                        "type": "string",
                        "description": "Fecha limite si el usuario la dio, tal cual "
                        "('mañana', 'el viernes', 'en 3 dias', '2026-10-01')",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "ID de la tarea SOLO si viene de una lista previa "
                        "(para complete/delete). No pongas aqui el titulo.",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Titulo de la tarea para complete/delete: SOLO el "
                        "nombre de la tarea (ej: 'comprar pilas'), nunca la instruccion "
                        "completa ('marca como realizada'). Tambien vale 'title'.",
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
            "description": "Lista Google Drive (o la boveda local si Google no esta "
            "conectado): carpetas, archivos o todo; o el contenido de "
            "una carpeta concreta con folder (nombre o id). Usalo cuando el "
            "usuario pregunte 'que tengo en mi drive', 'que carpetas tengo', "
            "'que hay dentro de la carpeta X', 'ensename mis archivos de drive'. "
            "NO mezcles: si pide carpetas, kind=folders; si pide archivos, "
            "kind=files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["all", "folders", "files"],
                        "description": "Que listar: all (todo), folders o files",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Carpeta cuyo contenido listar (nombre o id)",
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
    {
        "type": "function",
        "function": {
            "name": "run_backup",
            "description": "Lanza la copia de seguridad COMPLETA del servidor "
            "ahora (restic al USB de backup; tarda unos minutos y Rafita "
            "avisa por Telegram al terminar o fallar). Usalo cuando el "
            "usuario pida 'haz un backup', 'respaldame ahora', 'copia de "
            "seguridad del sistema' o similar. Para solo exportar SUS datos "
            "personales en JSON usa export_my_data.",
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
            "name": "get_backup_status",
            "description": "Estado de la ultima copia de seguridad COMPLETA "
            "del servidor: fecha, snapshot, tamanio, espacio libre en el USB, "
            "servicios omitidos y si hay una peticion reciente sin ejecutar. "
            "Usalo cuando pregunte si el backup va bien o cuando acabe de "
            "pedir un backup.",
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
            "name": "get_automation_runs",
            "description": "Historial de ejecuciones de las automatizaciones "
            "programadas (briefing, inbox, sync Google, radar, CRM, "
            "secuencias, informes...): fecha, resultado y error. Usalo cuando "
            "pregunten que automatizaciones han fallado, cuantas veces han "
            "corrido o como ha ido algo programado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Dias hacia atras (defecto 7)",
                    },
                    "only_errors": {
                        "type": "boolean",
                        "description": "true para ver solo los fallos",
                    },
                },
                "required": [],
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

    Nota (2026-09-30): el ranking por embeddings puede dejar fuera la tool
    correcta en frases cortas ("apunta que tengo que comprar pilas" excluia
    manage_google_tasks). Subir k a 15 empeoro la eleccion del modelo; el
    caso se cubre con la recuperacion del orquestador (si todas las tools
    fallan, el modelo elige en texto viendo el catalogo completo).
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


async def best_tools_for_message(text: str, k: int = 3) -> tuple[list[dict[str, Any]], float]:
    """Top-k herramientas por similitud y el score del top-1 (reintentos).

    El tool-calling de modelos pequenos es no determinista: a veces responden
    sin llamar a la herramienta aunque la peticion sea claramente una accion.
    El orquestador usa esto para decidir si merece la pena reintentar y con
    que herramientas forzar. Se devuelve una lista (no solo el top-1) porque
    el ranking por embeddings tiene ruido (~0.02 por el padding del batch):
    con top-3 la herramienta correcta casi siempre esta incluida y el modelo
    tiene menos donde perderse que con las 10. Devuelve ([], 0.0) si el
    embedding falla: en ese caso no se fuerza nada.
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
        return [], 0.0

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    ranked = sorted(
        tools,
        key=lambda t: dot(message_vec, _TOOL_EMBEDDINGS.get(t["function"]["name"], [])),
        reverse=True,
    )
    top = ranked[: max(1, min(k, len(ranked)))]
    score = dot(message_vec, _TOOL_EMBEDDINGS.get(top[0]["function"]["name"], []))
    return top, score


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
