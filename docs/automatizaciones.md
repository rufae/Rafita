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

Implementadas ya:
- **Deduplicación del Inbox** por id de correo (el flujo corre cada 30 min; sin
  esto avisaría del mismo correo en bucle).
- **Tiempo con AEMET** (clave en `.env`, municipio configurable con
  `BRIEFING_MUNICIPIO`) con respaldo a open-meteo.
- **Botones URL** en el briefing (tareas/calendario) y **flujo manual de
  prueba** (`Rafita - Briefing (prueba manual)`, desactivado) para re-lanzarlo
  a demanda.

Siguientes (por orden de valor):
1. **Botones con acción real**: `[Reagendar]` que abra el flujo de creación de
   evento, `[Enviar respuesta]` que dispare `send_gmail` con el borrador
   (requiere callback en Rafita, ya hay precedente con los gastos).
2. **Facturas por email → Drive**: extraer adjuntos PDF de los correos
   clasificados como factura y guardarlos en `Drive/Finanzas/<año>/Facturas`.
3. **Lista VIP de remitentes** (jefe/clientes) que fuerza categoría urgente y
   evita que el clasificador se equivoque.
4. **Briefing adaptativo**: fines de semana en modo resumen; encabezar el
   evento si empieza en <2 h; guardar copia del briefing como nota del vault.
5. **Avisos AEMET oficiales** (CAP): alertas naranjas/rojas al briefing.
6. **Radar de IA (punto 4)**: feeds RSS + GitHub trending → resumen de 3 items
   relevantes al stack (siguiente bloque a implementar).
7. **Informe semanal de infraestructura (punto 2B)**: estado de snapshots
   restic, disco, backups de Drive y actualizaciones pendientes, los domingos.
