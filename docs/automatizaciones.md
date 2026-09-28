# Automatizaciones

> **Estado (2026-09-28):** el **punto 1 está implementado y verificado en
> real** con n8n + Rafita:
> - **1A Briefing contextual** (`deploy/hp/n8n-flows/3-briefing-contextual.json`):
>   cada día a las 08:00, Rafita compone el briefing (agenda, tareas, correo,
>   **tiempo AEMET**, estado del servidor) y n8n lo entrega en Telegram con
>   botones URL. Verificado: mensaje real entregado.
> - **1B Inbox Zero** (`4-inbox-zero.json`): cada 30 min clasifica el correo no
>   leído (urgente/factura/cliente/informativo) y avisa solo si hay urgentes o
>   de clientes, **con borrador**; deduplicado por correo (no repite avisos).
>   Verificado contra el Gmail real.
> - **1C Captura a bóveda** (`5-captura-vault.json`): webhook → nota `.md` con
>   frontmatter y etiquetas en `00-Inbox/`. Verificado con nota real.
>   (La captura por voz/foto desde Telegram ya es nativa en Rafita.)
> - **Extras implementados (2026-09-28, verificados)**: botones con acción
>   (enviar borrador por Gmail, reagendar), briefing adaptativo (finde, evento
>   en <2 h, copia al vault), avisos AEMET CAP, radar de IA (`6-radar-ia.json`),
>   informe semanal de infraestructura (`7-informe-semanal.json`) y **briefing
>   en audio** (nota de voz estilo podcast).
>
> Arquitectura: **Rafita aporta datos + IA** (ya tiene OAuth de Google, vault y
> LLM local) y **n8n orquesta** disparos y entrega. Los endpoints del gateway
> (`/automation/briefing`, `/automation/inbox-scan`, `/automation/capture`) van
> firmados con HMAC y viven en la red interna `rafita-network`.

Aquí tienes una selección de automatizaciones de nivel profesional diseñadas específicamente para sacar el máximo partido a tu infraestructura (n8n + Rafita con IA local/Gemini + Google Workspace + nodos HP/Dell + Obsidian):

1. Productividad Ejecutiva y Asistente Personal
A. Briefing Matutino Contextual (08:00 AM)

    Flujo: Cron 07:00 AM → n8n lee Google Calendar + Google Tasks pendientes + correos clave en Gmail (últimas 12h) + estado del Home Server → Pasa la información cruda a Rafita → Rafita genera un resumen ejecutivo jerarquizado por prioridades.

    Acción: n8n envía el mensaje estructurado a Telegram con botones interactivos: [✅ Ver tareas] [📅 Reagendar eventos] [📝 Ver notas rápidas].

    Valor: Empiezas el día con la visión clara de tu agenda y pendientes sin tener que abrir 4 aplicaciones distintas.

    Sugerencias: Agregar API del tiempo para informarte del tiempo que hara en ese dia con avisos. (he puesto en el .env del proyecto actual la api AEMET_API_KEY)

B. Pipeline "Inbox Zero" e Inteligencia de Email

    Flujo: Webhook/Poll en Gmail ante nuevos correos entrantes → n8n filtra newsletters o spam → Pasa el cuerpo a Rafita → Rafita categoriza (Urgente, Factura, Cliente, Informativo) y redacta una respuesta borrador si lo requiere.

    Acción: Si es urgente, envía alerta a Telegram con un botón [🚀 Enviar respuesta sugerida]. Si contiene un PDF/Factura, lo extrae y lo guarda automáticamente en la carpeta correspondiente de Google Drive.

    Valor: Reduce drásticamente el tiempo empleado en gestionar la bandeja de entrada.

C. Captura Multimodal a Bóveda de Obsidian

    Flujo: Envías una nota de voz, texto largo, enlace o foto a Telegram → n8n intercepta el mensaje → Rafita transcribe (Whisper/STT) o procesa visión, extrae entidades y añade etiquetas YAML/Markdown.

    Acción: n8n escribe directamente el archivo .md formateado dentro de tu bóveda de Obsidian alojada en el servidor (a través del sistema de archivos local o API de Obsidian).

    Valor: Centraliza tus ideas, resúmenes de lectura y notas de voz en una base de conocimientos estructurada sin intervención manual.

2. DevOps, Monitoreo y Autocuración (Self-Healing Infra)
A. Guardián de Infraestructura y Autocuración de Contenedores

    Flujo: Webhook desde Glances, Uptime Kuma o script en el nodo HP/Dell cuando un contenedor Docker cae o el uso de RAM/VRAM supera el 95% → n8n recibe la alerta → Consulta a Rafita con el último log de Docker para diagnóstico.

    Acción: Rafita decide la acción correctiva (ej. docker restart container_name o limpiar caché de modelos). n8n ejecuta la orden SSH en el nodo afectado y notifica por Telegram: "El servicio X cayó por falta de memoria. Rafita lo ha reiniciado y el servicio está de nuevo Online [OK]".

    Valor: Mantiene tu servidor de producción y nodos de IA en alta disponibilidad de forma autónoma.

B. Informe Semanal de Salud, Seguridad y Backups

    Flujo: Cron los domingos a las 23:00 → n8n consulta el estado de los últimos snapshots de Restic, espacio en disco de NVMe/SATA, volumen de la base de datos SQLite/Postgres y actualizaciones del sistema pendientes.

    Acción: Genera un informe Markdown, lo guarda en Google Drive y te envía un resumen sintético a Telegram. Si el backup en Google Drive falló, lanza una alerta crítica.

    Valor: Tranquilidad absoluta sobre la integridad de tus datos y réplicas de seguridad.

3. Finanzas y Operaciones (Proyecto BuenaTierra / Freelance)
A. Contabilidad Automatizada y Conciliación

    Flujo: Subida de foto de ticket (vía el nuevo flujo de visión que has configurado) o recepción de factura PDF por email → n8n envía la imagen/PDF a Rafita (Visión) → Extrae JSON estructurado: {proveedor, cif, fecha, importe_base, iva, total, categoria}.

    Acción: n8n inserta la fila en Google Sheets / base de datos PostgreSQL de BuenaTierra, guarda el comprobante en Google Drive (Drive/Finanzas/2026/Facturas_Recibidas/) y te notifica por Telegram el balance del mes actualizado.

    Valor: Cero gestión manual de papel ni hojas de cálculo al final del mes o trimestre.

B. Creador y Enviador de Propuestas / Facturas

    Flujo: Comando en Telegram /propuesta cliente="Nombre" concepto="Desarrollo App" total=1500 → n8n recibe los parámetros → Rafita redacta los alcances del proyecto → n8n utiliza una plantilla de Google Docs / HTML-to-PDF para generar el documento oficial.

    Acción: Deposita el PDF en Google Drive, genera el borrador en Gmail listo para enviar y te devuelve el enlace directo por Telegram.

    Valor: Emisión de propuestas profesionales y facturas en menos de 30 segundos.

4. Vigilancia Tecnológica y Aprendizaje Activo
A. Radar de Inteligencia e Innovación en IA/Software

    Flujo: Cron diario → n8n consulta la API de GitHub (trending repos en Python, TypeScript, LangChain, Ollama, Docker) y feeds RSS de blogs técnicos de referencia.

    Acción: Rafita analiza las novedades, descarta el ruido y filtra solo las herramientas o repositorios que encajan con tu stack tecnológico (.NET, Python, IA multi-agente, RAG). Te envía una síntesis diaria de máximo 3 items relevantes con resumen de utilidad práctica.

    Valor: Te mantiene a la vanguardia de la industria sin perder tiempo navegando por redes o foros.

---

## Mejoras propuestas (profesionalización)

Implementadas y verificadas (2026-09-28):
- **Deduplicación del Inbox** por id de correo (el flujo corre cada 30 min; sin
  esto avisaría del mismo correo en bucle).
- **Tiempo con AEMET** (clave en `.env`, municipio con `BRIEFING_MUNICIPIO`) y
  **avisos oficiales CAP** (zona con `AEMET_AREA`, por defecto Madrid) que se
  destacan al principio del briefing.
- **Botones con acción real**: `[✉️ Responder]` en el Inbox envía el borrador
  por Gmail; `[⏰ Reagendar evento]` en el briefing muestra la agenda y permite
  mover un evento («mueve dentista al viernes a las 10» → acción `move`).
- **Briefing adaptativo**: fin de semana en modo resumen, evento en <2 h
  encabezando el mensaje y copia diaria en el vault (`Briefings/`).
- **Radar de IA** (punto 4): GitHub (repos nuevos con más estrellas) + RSS
  (Hugging Face, Real Python, n8n) filtrado por el LLM → 3 items prácticos.
- **Informe semanal de infraestructura** (punto 2B): backups (estado real
  escrito por los scripts), restore-drill, disco, BD y servicios; alertas
  críticas y **sin inventar datos** si algo falta.
- **Briefing en audio**: nota de voz (piper) estilo podcast para escucharlo
  mientras haces otras cosas.

Siguientes (por orden de valor):
1. **Facturas por email → Drive**: extraer adjuntos PDF de los correos
   clasificados como factura y guardarlos en `Drive/Finanzas/<año>/Facturas`.
2. **Lista VIP de remitentes** (jefe/clientes) que fuerza categoría urgente.
3. **Contabilidad automática (punto 3A)** y **propuestas/facturas (3B)**.
4. **Guardián de infraestructura con autocuración (2A)**: reinicio de
   contenedores caídos con aviso y log del diagnóstico.

---

## Automatizaciones avanzadas (catálogo para elegir)

Las tres que pediste, desarrolladas, más ejemplos del mismo nivel. Todas
gratuitas y encajan con la arquitectura actual (Rafita = datos + IA; n8n =
disparo + entrega; endpoints HMAC `/automation/*`).

### A. Sincronización e integración inteligente Google ↔ bóveda
- **Qué**: cada hora (y a demanda) los eventos, tareas, correos destacados y
  documentos recientes se reflejan como notas enlazadas en el vault; y al
  revés, una tarea creada en una nota se crea en Google. Conflictos con
  regla por campo (gana el más reciente) y registro de cambios.
- **Cómo**: endpoint `/automation/sync` + tarea programada n8n; usa
  `list_calendar_events`, `list_tasks`, `search_gmail`, `list_drive` y
  `create_task`/`move_event`. Ya existe la base (`/sync_google` unidireccional).
- **Valor**: una sola fuente de verdad consultable offline y por el RAG.
- **Esfuerzo**: L (resolución de conflictos y pruebas).

### B. Secuencias de email automáticas con personalización dinámica
- **Qué**: plantillas con variables (nombre, proyecto, última interacción) y
  envío escalonado (día 0, 3, 7); la secuencia se **detiene** si el contacto
  responde; cada envío queda registrado en su nota.
- **Cómo**: n8n gestiona la secuencia; Rafita redacta cada correo con contexto
  (`send_gmail`) y detecta respuestas con `search_gmail`.
- **Valor**: seguimiento de propuestas y onboarding sin trabajo manual.
- **Esfuerzo**: M.

### C. Ventas y gestión de clientes (mini-CRM en el vault)
- **Qué**: cada cliente es una nota con estado (lead/propuesta/cerrado),
  valor, último contacto y próximos pasos; el pipeline se resume a demanda;
  recordatorios de seguimiento automáticos; propuestas en PDF (Google Docs)
  y conciliación de ingresos con finanzas.
- **Cómo**: notas con frontmatter + endpoint de resumen + flujos n8n de
  recordatorio; Rafita responde «¿cómo va el pipeline?» con una tabla.
- **Valor**: CRM ligero, privado y sin cuotas.
- **Esfuerzo**: L.

### D. Más ejemplos del mismo nivel
1. **Facturas por email → Drive + contabilidad**: adjuntos PDF a
   `Finanzas/<año>/Facturas` y registro del gasto (base ya hecha con tickets).
2. **Guardián de infraestructura con autocuración**: contenedor caído → Rafita
   diagnostica el log → n8n reinicia y avisa (punto 2A).
3. **Revisión semanal GTD**: domingos, correo/tareas/vault → plan de la semana
   con prioridades y huecos de agenda.
4. **Actas de reunión**: audio → transcripción → resumen + tareas + nota
   enlazada al evento.
5. **Detección de suscripciones y cobros recurrentes** desde Gmail → aviso
   mensual y propuesta de cancelación.
6. **Onboarding de cliente**: carpeta en Drive + nota CRM + tarea + email de
   bienvenida en un solo comando (`/nuevo_cliente Nombre`).
7. **Monitor de precios/licitaciones**: RSS/scraping → aviso si baja el precio
   o sale una oportunidad que encaja con tu perfil.
8. **Pipeline de contenido**: ideas del vault → borradores → calendario de
   publicación → recordatorio.

---

## Pendientes documentados (para hacer más adelante)

### Voz más humana (mejora futura)
- **Ya hecho**: voz por defecto `es_ES-davefx-medium` (calidad media) y
  limpieza de texto (sin Markdown, emojis ni símbolos; unidades normalizadas
  «24 h» → «24 horas»). Configurable con `TTS_VOICE`.
- **Opciones siguientes** (gratis, local):
  1. Voz **`high`** de Piper cuando exista para español (p. ej. `es_MX-claude-high`)
     o voces `medium` alternativas (`es_ES-sharvard-medium`) — comparar y elegir.
  2. **XTTS v2 / Coqui TTS** local: clonación de voz y prosodia más natural
     (más CPU/GPU; probar en la torre).
  3. **SSML-lite**: pausas y énfasis insertando comas/puntos suspensivos;
     velocidad con `length_scale` de Piper (añadir `TTS_SPEED` al .env).
  4. Trocear por frases con pausas naturales y respetar signos (ya se hace por
     fragmentos en el modo llamada).

### Automatizaciones aún no implementadas (catálogo A-D)
- B. Secuencias de email con personalización dinámica.
- C. Mini-CRM de ventas y gestión de clientes.
- D1. Facturas por email → Drive + contabilidad.
- D2. Guardián de infraestructura con autocuración (2A).
- D3. Revisión semanal GTD, actas de reunión, suscripciones recurrentes,
  onboarding de cliente, monitor de precios/licitaciones, pipeline de contenido.

### Otras mejoras pendientes
- RAG con bóveda real y reranking (híbrido BM25 + vectorial).
- Métricas de coste/latencia por usuario y alertas proactivas de infraestructura.
- Onboarding guiado (`/start`) que pregunte ubicación, Google, vault y
  preferencias en pasos.
- i18n completo, PWA de chat+voz y dispositivo de voz dedicado.
