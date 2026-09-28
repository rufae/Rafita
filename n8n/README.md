# Flujos n8n de Rafita

Plantillas listas para importar, **sin secretos ni datos personales**: los
valores sensibles se sustituyen desde el `.env` del proyecto.

## Contenido

| Fichero | Categoría | Disparador | Qué hace |
|---|---|---|---|
| `01-briefing-contextual.json` | Briefing | Cron 08:00 | Pide el briefing a Rafita (agenda, tareas, correo, tiempo, avisos, servidor), lo entrega en Telegram con botones y manda la nota de voz. |
| `02-inbox-zero.json` | Correo | Cada 30 min | Clasifica el correo no leído, avisa de urgentes/clientes con borrador y deduplica. |
| `03-captura-vault.json` | Bóveda | Webhook | Guarda una idea/nota en `00-Inbox/` con frontmatter y etiquetas. |
| `04-sync-google-vault.json` | Google | Cada hora | Sincroniza tareas/calendario Google ↔ bóveda y refresca notas; avisa solo si hay cambios. |
| `05-informe-semanal.json` | Infra | Domingo 23:00 | Informe de backups, disco, BD y servicios con alertas; resumen a Telegram. |
| `06-radar-ia.json` | Radar | Cron 09:00 | Busca novedades (GitHub/RSS), las filtra con IA y guarda las mejores en la bóveda. |
| `07-ejecutable-chat-voz.json` | Automatización | Webhook | Permite lanzar un flujo desde el chat o la llamada de Rafita. |
| `08-plantilla-aviso-programado.json` | Plantillas | Cron (inactivo) | Ejemplo mínimo: aviso programado a Telegram. |
| `09-crm-seguimiento.json` | Clientes | Lunes 09:00 | Avisa de clientes del CRM sin contacto reciente o con seguimiento vencido. |

Los flujos están etiquetados por categoría (Briefing, Correo, Bóveda, Google,
Infra, Radar, Automatización, Plantillas) para poder filtrarlos en n8n.
n8n no permite carpetas en la versión comunitaria (es función de pago), por eso
se usa **etiqueta + prefijo de categoría en el nombre**.

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

## Seguridad

- Las plantillas no contienen tokens, chat ids ni IPs privadas: hay un test
  (`agent/tests/test_n8n_templates_sanitized.py`) que lo verifica en cada gate.
- Los flujos hablan con Rafita por la red interna de Docker
  (`http://rafita-agent-core:8000`) y firman con HMAC usando `WEBHOOK_SECRET`.
