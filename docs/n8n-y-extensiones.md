# n8n y extensiones de Rafita AVP (propuesta, 2026-09-27)

Todo lo de este documento es **gratuito** salvo lo marcado explícitamente.
n8n es software libre (licencia fair-code; self-hosted con Docker, sin coste).
Estado: PROPUESTA — el propietario elige qué se implementa.

## 1. Integración con n8n (contenedor propio en el HP)

Rafita ya tiene gateway HTTP con webhooks firmados (HMAC-SHA256) y n8n puede
correr en el mismo HP. Tres direcciones posibles:

### A. n8n → Rafita (avisos y resúmenes)
- n8n dispara flujos (cron, RSS, correo, eventos) y hace `POST
  /webhook/{source}` del gateway con firma HMAC.
- Rafita lo muestra en Telegram como aviso proactivo (funciona HOY, sin
  cambios de código; solo montar n8n y crear los flujos).
- Casos: «precio de X ha bajado», «nuevo correo importante», «hoy llueve»,
  «entrega pendiente».

### B. Rafita → n8n (ejecutar tus automatizaciones)
- Nueva herramienta `trigger_n8n(workflow, payload)` que llama al webhook
  de entrada de n8n (`POST https://n8n.../webhook/<id>`).
- Desde chat o **voz**: «ejecuta la automatización de facturas», «mándale
  el informe a mamá por tu flujo de correo».
- Cambio necesario: 1 tool + 1 endpoint en n8n + secretos en `.env`.

### C. n8n como hub de conectores → segundo cerebro
- n8n scrapea/filtra (IMAP, Sheets, webs, APIs) y hace `POST /webhook/brain`
  con el texto; Rafita lo ingesta en el vault (`ingest_file`) indexado al
  instante.
- Casos: facturas del banco, artículos guardados, notas de apps externas.

### D. Briefing matutino (A+C combinados)
- Cron de n8n a las 08:00 → agenda de Google + correo + tiempo → webhook →
  Rafita redacta el resumen del día → mensaje de voz o texto en Telegram.

**Seguridad**: webhooks con HMAC (ya implementado), n8n solo accesible por
Tailscale/interfaz interna, API key de n8n en `.env` (nunca en git).

## 2. Menú de extensiones (elige y la implemento)

| # | Extensión | Coste | Descripción breve |
|---|---|---|---|
| 1 | **Conector n8n bidireccional** | 0 € | Bloques A+B anteriores: tus automatizaciones se disparan y se ejecutan desde el chat/voz |
| 2 | **Briefing matutino** | 0 € | Resumen diario automático (agenda, correo, tiempo) en Telegram o voz |
| 3 | **Gastos por foto** | 0 € | Foto de ticket/factura → visión local (llava) → `save_expense` categorizado |
| 4 | **Domótica ampliada** | 0 € | Home Assistant: escenas, rutinas y control por voz («luces al 30%») |
| 5 | **PWA de chat+voz** | 0 € | Evolucionar `web/call_rafita.html` a app instalable con chat y llamadas |
| 6 | **Recordatorios proactivos** | 0 € | Rafita analiza tu agenda y te avisa antes de que algo se te olvide |
| 7 | **Multiusuario con roles** | 0 € | Whitelist + roles (owner/invitado) para que más personas usen tu instancia |
| 8 | **WhatsApp (Evolution API)** | 0 €* | Escribir/recibir WhatsApp; *riesgo de bloqueo del número (no oficial)* |
| 9 | **Sync bidireccional Google** | 0 € | Notas→tareas/eventos de Google y vuelta, con resolución de conflictos |
| 10 | **Grafo de conocimiento** | 0 € | Relaciones tipadas entre notas, personas y proyectos («quién estuvo en…») |
| 11 | Contestador de llamadas | ~1,5-6 €/mes | Ya implementado el software; **exigiría un número SIP de pago** (no 100 % gratis) |

Recomendación de partida (todo gratis, alto valor): **1 + 2 + 6** — tus
automatizaciones de n8n, un briefing diario y recordatorios inteligentes.

## 3. Cómo se montaría n8n en el HP (cuando se elija)

1. Servicio `n8n` en un overlay de compose (puerto interno, sin exponer a la
   LAN; acceso vía Tailscale o túnel), volumen `n8n_data`.
2. `N8N_ENCRYPTION_KEY` y `EVOLUTION/WEBHOOK_SECRETS` en `.env`.
3. Flujos de ejemplo importables (JSON) para los bloques A-D.
4. README con los pasos y los riesgos de seguridad.
