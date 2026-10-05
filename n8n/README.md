# Flujos n8n de Rafita

Plantillas listas para importar, **sin secretos ni datos personales**: los
valores sensibles se sustituyen desde el `.env` del proyecto.

## Contenido

| Fichero | Categoría | Disparador | Qué hace |
|---|---|---|---|
| `01-briefing-contextual.json` | Briefing | Cada día 08:00 | Pide el briefing a Rafita (agenda, tareas, correo, tiempo, avisos, servidor), lo entrega en Telegram con botones y manda la nota de voz. |
| `02-inbox-zero.json` | Correo | Cada 30 min | Clasifica el correo no leído, avisa de urgentes/clientes con borrador y deduplica. |
| `03-captura-vault.json` | Bóveda | Webhook `captura-vault` | Guarda una idea/nota en `00-Inbox/` con frontmatter y etiquetas. |
| `04-sync-google-vault.json` | Google | Cada hora | Sincroniza tareas/calendario Google ↔ bóveda y refresca notas; avisa solo si hay cambios. |
| `05-informe-semanal.json` | Infra | Domingos 23:00 | Informe de backups, disco, BD y servicios con alertas; resumen a Telegram. |
| `06-radar-ia.json` | Radar | Cada día 09:00 | Busca novedades (GitHub/RSS), las filtra con IA y guarda las mejores en la bóveda. |
| `07-ejecutable-chat-voz.json` | Automatización | Webhook `ejemplo-rafita` | Permite lanzar un flujo desde el chat o la llamada de Rafita (`trigger_n8n`). |
| `08-plantilla-aviso-programado.json` | Plantillas | Cada día 09:00 (inactivo) | Ejemplo mínimo: aviso programado a Telegram. |
| `09-crm-seguimiento.json` | Clientes | Lunes 09:00 | Avisa de clientes del CRM sin contacto reciente o con seguimiento vencido. |
| `10-secuencias-email.json` | Secuencias | Cada día 09:30 | Envía los emails vencidos de las secuencias y avisa si algún contacto respondió (secuencia detenida). |

Los flujos están etiquetados por categoría (Briefing, Correo, Bóveda, Google,
Infra, Radar, Automatización, Plantillas) para poder filtrarlos en n8n.
n8n no permite carpetas en la versión comunitaria (es función de pago), por eso
se usa **etiqueta + prefijo de categoría en el nombre**.

---

## Detalle de cada automatización

Todos los flujos firman con HMAC (`WEBHOOK_SECRET`) las llamadas que van al
gateway de Rafita (`/automation/*`, `/api/n8n/run`) y traen un webhook
«Manual (chat)» (`manual-<nombre>`) para lanzarlos desde el orquestador.
Cada sección indica sus **dependencias**: sin ellas el flujo degrada (lo
reporta en `automation_runs`) en vez de tumbarse.

### 01 · Briefing contextual — cada día a las 08:00
- **Disparador**: cron diario `08:00`.
- **Cómo se ejecuta**: n8n llama a `POST /automation/briefing` de Rafita
  (firma HMAC). Rafita compone el briefing real: próximos eventos, tareas
  con vencimientos, correos importantes, tiempo (AEMET) y estado del servidor.
- **Resultado**: mensaje en Telegram con botones de acción y **nota de voz**
  con el resumen (`/automation/send-voice`). El filtro «Mensaje Telegram»
  no envía nada si Rafita falla o no devuelve texto (sin `undefined`).
- **Dependencias**: Google conectado (agenda/tareas/correo; con la BD local
  degrada a lo disponible), AEMET (tiempo/avisos), Ollama (redacción),
  `TELEGRAM_TOKEN`, `WEBHOOK_SECRET`.
- **Probarlo**: *Execute Workflow* en n8n, o `POST /automation/briefing` con
  cabecera `X-Webhook-Signature` (HMAC-SHA256 del cuerpo).

### 02 · Inbox Zero (correo) — cada 30 minutos
- **Disparador**: cron cada 30 min.
- **Cómo se ejecuta**: consulta Gmail no leído, clasifica cada mensaje con IA
  (urgente / cliente / factura / personal / informativo / sin_accion),
  los ordena por prioridad y **deduplica** por id de correo.
- **Resultado**: aviso en Telegram solo de urgentes y clientes, con un
  borrador de respuesta sugerido (con botón «✉️ Responder»). El resto no
  interrumpe.
- **Dependencias**: cuenta de Google conectada (`/setup_google`), Ollama,
  `TELEGRAM_TOKEN`, dedup en la BD (KV).

### 03 · Captura a bóveda — vía webhook
- **Disparador**: `POST http://n8n:5678/webhook/captura-vault` con
  `{"text": "...", "title": "...", "tags": [...]}`.
- **Cómo se ejecuta**: el nodo «Firmar HMAC» normaliza el cuerpo y lo manda a
  `POST /automation/capture` de Rafita; genera frontmatter (fecha, etiquetas)
  y guarda la nota en `00-Inbox/`; el watchdog la indexa al instante.
- **Resultado**: nota `.md` nueva y disponible para búsquedas con citas.
- **Dependencias**: bóveda escribible, `WEBHOOK_SECRET`.
- **Probarlo**: `curl -X POST http://127.0.0.1:5678/webhook/captura-vault
  -H 'Content-Type: application/json'
  -d '{"text":"idea para el proyecto X","tags":["idea"]}'`
  (o contra el gateway con HMAC:
  `POST rafita-agent-core:8000/automation/capture` + `X-Webhook-Signature`).

### 04 · Sync Google ↔ bóveda — cada hora
- **Disparador**: cron cada hora.
- **Cómo se ejecuta**: pide a Rafita el sync (`/automation/sync`): contactos,
  eventos, tareas, Drive y correo se vuelcan/actualizan en notas locales y se
  marcan cambios; degrada por fuente (si una sección cae, las demás siguen).
- **Resultado**: aviso por Telegram **solo si hay cambios**; si no, silencio.
- **Dependencias**: Google conectado (permisos Calendar/Drive/Tasks/Gmail/
  People), bóveda, `TELEGRAM_TOKEN`.

### 05 · Informe semanal de infraestructura — domingos 23:00
- **Disparador**: cron semanal (domingo 23:00).
- **Cómo se ejecuta**: lee los ficheros de estado reales de los scripts
  (`backup-status.json`, `restore-drill-status.json`), disco, tamaño de BD,
  servicios y **notas nuevas de la bóveda (7 días)**, y los pasa por IA para
  redactar el informe.
- **Resultado**: informe en Telegram con alertas destacadas al principio y
  bloque «📚 Conocimiento nuevo» con enlaces a la PWA (`PWA_BASE_URL`).
- **Dependencias**: ficheros de estado (backup/restore-drill), Ollama,
  `TELEGRAM_TOKEN`; `PWA_BASE_URL` opcional (enlaces).

### 06 · Radar de IA — cada día 09:00
- **Disparador**: cron diario `09:00`.
- **Cómo se ejecuta**: recorre fuentes (GitHub/RSS), filtra las novedades con
  IA según tus intereses y selecciona las mejores.
- **Resultado**: nota en la bóveda con lo más relevante del día + aviso en
  Telegram solo si hay radar real (filtro «Solo si hay radar»).
- **Dependencias**: salida a internet (GitHub/RSS), Ollama, bóveda,
  `TELEGRAM_TOKEN`, `WEBHOOK_SECRET`.

### 07 · Ejecutable desde chat/voz — vía webhook
- **Disparador**: `POST http://n8n:5678/webhook/ejemplo-rafita` (webhook
  abierto en la red interna; el webhook de n8n no verifica HMAC — la firma
  HMAC la hacen los flujos al llamar al gateway de Rafita).
- **Cómo se ejecuta**: es el destino de la herramienta `trigger_n8n`; puedes
  decirle a Rafita «ejecuta la automatización de X» por chat o en la llamada
  y este flujo se dispara.
- **Resultado**: lo que defina el flujo (aviso, informe, correo…).
- **Dependencias**: `N8N_WEBHOOKS` (mapa nombre→URL en el `.env`).

### 08 · Plantilla de aviso programado — cada día 09:00 (inactivo)
- **Disparador**: cron diario `09:00` (importado **pausado** a propósito:
  `NO_ACTIVAR` en `n8n_import_flows.py`; actívalo solo si lo quieres).
- **Cómo se ejecuta**: ejemplo mínimo para copiar: manda un aviso fijo a
  Telegram. Útil como base para tus propios recordatorios.
- **Dependencias**: `TELEGRAM_TOKEN`.

### 09 · Seguimiento de clientes (CRM) — lunes 09:00
- **Disparador**: cron semanal (lunes 09:00).
- **Cómo se ejecuta**: revisa el CRM de Rafita (`/clientes`) y detecta
  clientes sin contacto reciente o con seguimiento vencido.
- **Resultado**: lista de pendientes en Telegram para empezar la semana.
- **Dependencias**: CRM con clientes, `TELEGRAM_TOKEN`.

### 10 · Secuencias de email — cada día 09:30
- **Disparador**: cron diario `09:30`.
- **Cómo se ejecuta**: revisa las secuencias de email (plantillas de
  seguimiento con días de espera) y envía las que tocan; se detienen solas
  si el contacto responde.
- **Resultado**: emails enviados + aviso si algún contacto respondió.
- **Dependencias**: Gmail conectado, secuencias creadas (BD), `TELEGRAM_TOKEN`.

---

## Robustez y observabilidad (Fase 1, 2026-10-04)

Endurecimiento aplicado a **todos** los flujos (con `test_n8n_robustez.py`
en el gate):

- **Reintentos con backoff**: todo nodo HTTP reintenta 3 veces con 2 s de
  espera (`retryOnFail`/`maxTries`/`waitBetweenTries`).
- **Timeouts**: 30 s para las llamadas a Rafita, 15 s para Telegram, 10 s
  para el informe. Nada se queda colgado.
- **Fallo controlado**: `onError=continueRegularOutput` en las llamadas a
  Rafita y a Telegram. Si un servicio cae, el flujo **degrada** (los filtros
  posteriores no avisan con basura, y el nodo «Mensaje Telegram» no manda
  `undefined`) en vez de tumbarse ni spamear.
- **Informe de cada ejecución**: una rama paralela con «Firmar informe» +
  «Reportar a Rafita» envía `{execution_id, workflow, status, error,
  finished_at}` firmado con HMAC a `POST /api/n8n/run`. Rafita lo guarda en
  la tabla `automation_runs` (**idempotente por `execution_id`**: los
  reintentos no duplican filas).
- **Alertas sin spam**: solo se avisa a los admins si la **misma**
  automatización falla **dos veces seguidas**.
- **Respuesta con datos reales**: desde el chat, «¿qué automatizaciones han
  fallado esta semana?» usa la tool `get_automation_runs` (últimos N días,
  solo errores opcional).

> Al actualizar las plantillas, **reimporta los flujos** en n8n (sección
> *Importar*): las copias activas en tu instancia n8n no se actualizan solas.

## Requisitos (.env)

| Variable | Para qué |
|---|---|
| `N8N_API_KEY` | Importar/actualizar por API (`scripts/n8n_import_flows.py`). |
| `WEBHOOK_SECRET` | Firma HMAC de las llamadas a Rafita (`/automation/*`). |
| `TELEGRAM_TOKEN` | Enviar mensajes/notas de voz. |
| `ADMIN_IDS` | Chat de destino (el primer id). |
| `N8N_WEBHOOKS` | Mapa nombre→URL para lanzar flujos desde el chat (`{"ejemplo": "http://n8n:5678/webhook/..."}`). |

## Importar

```bash
# n8n debe estar levantado y con la red de Rafita
python scripts/n8n_import_flows.py                 # crea/actualiza flujos y etiquetas
python scripts/n8n_import_flows.py --activate      # además activa por CLI (en el host de n8n)
```

También puedes importarlos a mano: en n8n → *Workflows* → *Import from File*.
Si lo haces a mano, sustituye antes los placeholders
`PEGA_AQUI_TU_WEBHOOK_SECRET`, `PEGA_AQUI_TU_BOT_TOKEN` y
`PEGA_AQUI_TU_CHAT_ID` por tus valores.

La **activación** no está en la API pública:

```bash
docker exec n8n n8n update:workflow --id=<id> --active=true
docker restart n8n
```

## Verificación rápida

1. **Briefing**: espera a las 08:00 o lanza el flujo a mano desde n8n
   (*Execute Workflow*) y comprueba que llega el mensaje + la nota de voz.
2. **Inbox Zero**: envíate un correo con asunto «URGENTE prueba» y en ≤30 min
   debería llegar el aviso (solo la primera vez: deduplica por id).
3. **Captura**: prueba el `curl` de la sección 03 y comprueba la nota en
   `00-Inbox/`.
4. **Informe semanal**: *Execute Workflow* del 05 y revisa el informe.
5. **Informe de ejecución**: al terminar cualquier flujo, pregunta a Rafita
   «¿qué automatizaciones han corrido?» y debe listar la ejecución (tabla
   `automation_runs`, sin duplicados aunque repitas el informe).

## Seguridad

- Las plantillas no contienen tokens, chat ids ni IPs privadas: hay un test
  (`agent/tests/test_n8n_templates_sanitized.py`) que lo verifica en cada gate.
- Los flujos hablan con Rafita por la red interna de Docker
  (`http://rafita-agent-core:8000`) y firman con HMAC usando `WEBHOOK_SECRET`.
