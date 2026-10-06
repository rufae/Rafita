# Modelo de amenazas v1 — Rafita AVP

Formato **STRIDE** por par *activo / frontera*. Cada mitigación citada está
verificada en el código del repositorio; si no se encontró mitigación, la fila
se marca **abierto** o **verificar**. Este documento complementa
[docs/SECURITY.md](SECURITY.md) (alcance, cifrado en reposo y red) y
[architecture.md](architecture.md).

---

## 1. Actores amenazantes

| Actor | Vector | Motivación / capacidad |
|---|---|---|
| Atacante remoto vía **prompt-injection desde documentos** | Notas del vault, PDF/DOCX/TXT/CSV ingeridos, contenido devuelto por búsqueda web o por una herramienta | Consigue que el modelo ejecute herramientas (escritura en la bóveda, envío de correo, lanzamiento de automatizaciones) o filtre contenido |
| Atacante remoto vía **prompt-injection en el mensaje** | Chat de Telegram, chat web, entrada de voz | Mismo objetivo, pero con la ventaja de que el mensaje llega con el rol `user` |
| **Robo de token de Telegram** | `.env`, fichero secreto, logs, copias, fugas en git | Control total del bot: leer el historial y emitir órdenes como si fueran del usuario |
| **Acceso a red local / tailnet** | Puertos expuestos, Ollama, gateway, servidor de voz | Lectura/uso del LLM, del gateway o de la voz si algo quedó fuera de `127.0.0.1` |
| **Acceso físico al USB de backup** | Robo/pérdida del disco externo | Lectura del repositorio de backup (incluye vault y `.env`) |
| **Dependencias comprometidas** | `pip`, imágenes Docker, actions de GitHub | Inyección de código en el build o en el despliegue |
| **Usuario web no autorizado / registro masivo** | Endpoints `/api/auth/*` | Obtener sesión JWT o destruir datos con `/api/gdpr/delete` |

## 2. Activos

| Activo | Dónde vive | ¿Cifrado? |
|---|---|---|
| Token de Telegram | `.env` o fichero `<VAR>_FILE` | No (proteger en disco/backup) |
| Credenciales OAuth/Google y API keys | `.env`, `credentials/`, columnas cifradas de SQLite | Parcial: Fernet en `credentials.value_enc` y `app_connectors.credentials_enc` |
| Bóveda Obsidian (segundo cerebro) | Markdown en disco | No (se recomienda cifrado de disco) |
| Base de datos SQLite | `data/db/rafita.db` | **No a nivel de BD**; sólo columnas puntuales con Fernet |
| `.env` (incluida `ENCRYPTION_KEY`) | Raíz del proyecto | No; sí fuera de git y con gitleaks |
| USB de backup | Repositorio restic en el USB | Sí (restic cifrado; contraseña root-only) |
| Panel web (sesión JWT, usuarios) | `web_users` en SQLite | Contraseñas con scrypt + sal; JWT HS256 firmado con `WEB_AUTH_SECRET` |
| Historial de chat y métricas | SQLite + `/metrics` | No |

## 3. Tabla STRIDE

Estado: **mitigado** / **parcial** / **abierto**.

| # | Activo | Frontera | Amenaza (STRIDE) | Mitigación existente (verificada) | Estado | Qué queda abierto y por qué |
|---|---|---|---|---|---|---|
| 1 | Token de Telegram | `.env` / fichero secreto ↔ proceso y git | **T**ampering · **I**nfo disclosure · **S**poofing | `<VAR>_FILE` (`config.py`, fail-closed); `.env` en `.gitignore`; gitleaks en CI (`ci.yml`); redacción de tokens en logs (`logger.redact_text`); bot privado por `ADMIN_IDS` (`access_control.is_allowed_user`) | Parcial | El `.env` sigue en claro en disco y viaja en el volumen del backup; quien lea el host lee el token |
| 2 | Credenciales OAuth Google / API keys | `.env` y SQLite ↔ proceso | **I**nfo disclosure | Fernet con `security_manager.encrypt_value`; `<VAR>_FILE` para `GOOGLE_WEB_CLIENT_SECRET`; `credentials/` fuera de git | Parcial | La clave Fernet vive en el mismo `.env` que la BD: el cifrado sólo protege ante robo de la BD aislada, no del host completo |
| 3 | Bóveda Obsidian | Disco ↔ herramientas del modelo | **T**ampering · **I**nfo disclosure | Confinamiento de rutas `path_safety.resolve_within`; `WRITE_TOOLS` bloqueables con `PERSIST_TO_BRAIN=false`; sin sincronización a terceros | Abierto | Texto plano: cualquier proceso con permisos de lectura del host (o un `run_automation` malicioso en n8n) accede al segundo cerebro |
| 4 | Base de datos SQLite | Fichero ↔ proceso/host | **I**nfo disclosure | Sin cifrado de BD; sólo `credentials`/`app_connectors` cifrados por columna | Abierto | Historial de chat, finanzas y preferencias en claro; mitigación prevista = cifrado de disco (`docs/SECURITY.md`) |
| 5 | Webhooks de n8n y automatizaciones (`/automation/*`, `/api/n8n/run`, `/webhook/{source}`, `/connector/{name}`, `/gmail/check`, `/homeassistant/*`, `/call`) | Red ↔ gateway | **S**poofing · **T**ampering · **E**levation | HMAC-SHA256 en `X-Webhook-Signature` con `hmac.compare_digest`, **fail-closed** (503 sin secreto, 401 con firma inválida); GET protegidos con `X-Webhook-Token` | Mitigado | Ninguno relevante: todos los `POST` del gateway llevan firma (verificado endpoint a endpoint) |
| 6 | Catálogo de automatizaciones n8n | Modelo ↔ n8n | **E**levation | Catálogo cerrado `AUTOMATIONS` con claves estables; permisos por modo `read`/`write`/`execute` y `confirm=true` obligatorio en `execute` (`core/orchestration.py`) | Mitigado | Depende de que n8n esté en red privada y de que los flujos firmen de vuelta con HMAC |
| 7 | **Panel web (SPA) — XSS** | Navegador ↔ salidas del LLM/usuario | **T**ampering (XSS) | `renderizarMarkdown` escapa `&`, `<`, `>` **antes** de generar HTML (`web/app/app.js`); CSP restrictiva (`script-src 'self'`, `frame-ancestors 'self'`, `object-src 'none'`) y `X-Content-Type-Options: nosniff` en el gateway; `textContent` para texto de usuario | Mitigado | El markdown es un renderer propio (sin librería de sanitización); si se añaden enlaces/HTML en el futuro sin escapar, reaparece el riesgo |
| 8 | Panel web — autenticación | Red ↔ `/api/auth/*` | **S**poofing · **E**levation | Contraseñas con scrypt + sal; JWT HS256 con caducidad de 7 días, **fail-closed** sin `WEB_AUTH_SECRET`; `require_admin` en rutas de administración; `WEB_ALLOW_REGISTRATION=false` por defecto; primer admin desde variables de entorno | Parcial | **Sin rate limit ni bloqueo de intentos en `/api/auth/login`**: la fuerza bruta sólo la frena el tamaño del secreto |
| 9 | Panel web — API en general | Red ↔ `/api/*` | **D**oS | Autenticación obligatoria (`require_user`) en chat, bóveda, reuniones y GDPR; `confirm=true` en `/api/gdpr/delete` | Abierto | No se encontró límite de tasa ni límite de tamaño de cuerpo en el gateway; los puertos están en `127.0.0.1`, lo que acota el alcance a la red local |
| 10 | **Documentos indexados (vault e ingesta)** | Contenido de terceros ↔ orquestador | **T**ampering (prompt injection) | No se encontró ninguna sanitización ni aislamiento del contenido recuperado; sólo aplican las guardias de honestidad, que impiden **afirmar** acciones no hechas pero no impiden **ejecutar** acciones inducidas | **Abierto** | Un chunk con instrucciones ocultas puede inducir al modelo a invocar herramientas de escritura o a lanzar automatizaciones `execute` (éstas sí requieren `confirm=true`, lo que mitiga parcialmente el impacto) |
| 11 | Mensajes de usuario (Telegram/web/voz) | Canal de entrada ↔ orquestador | **T**ampering (prompt injection) | Whitelist `ADMIN_IDS`; rate limit 20/min por chat y 30/min por usuario (`chat_limiter`, `RateLimiter`); guardias de honestidad; herramientas limitadas al catálogo y desconocidas → `unknown_tool` | Parcial | El prompt injection del propio usuario sólo está limitado por el catálogo de herramientas; el rate limit no existe en el canal web |
| 12 | USB de backup | Medio físico ↔ repositorio restic | **I**nfo disclosure | Repositorio **restic cifrado**; contraseña root-only en el host; `usb-eject` para retirada; restore-drill mensual (`deploy/hp/backup/`) | Mitigado | Las **copias horarias de la BD** (`hourly-db.sh`) y el stage viven en claro en el host; si el host queda comprometido, esas copias quedan expuestas |
| 13 | Motor de IA (Ollama) y red | Red local/tailnet ↔ nodo de IA | **E**levation · **I**nfo disclosure | `ufw` con deny por defecto; tráfico entre nodos por WireGuard (Tailscale); gateway y voz en `127.0.0.1` | Parcial | `docs/SECURITY.md` documenta que la regla de firewall del nodo de IA queda **sombreada** por la cadena de Tailscale: el motor es accesible desde cualquier dispositivo autenticado en la tailnet. Riesgo aceptado y anotado |
| 14 | Dependencias y build | CI/CD ↔ pip, Docker, Actions | **T**ampering | `pip-audit` como gate bloqueante (con excepciones explícitas de ChromaDB), gitleaks, dependabot semanal, test de invariante "solo ChromaDB embebido" | Parcial | Sigue habiendo avisos aceptados de ChromaDB sin fix upstream y paquetes con actualizaciones ignoradas por compatibilidad (`dependabot.yml`) |
| 15 | Métricas y logs | Proceso ↔ red local | **I**nfo disclosure · **R**epudiation | Puertos en `127.0.0.1`; redacción de secretos en logs (`RedactingFilter`) y en `tail_logs`; correlación por `correlation_id` y métricas por herramienta en `/metrics` | Parcial | `/metrics`, `/health` y `/ready` no exigen autenticación: quien tenga acceso al puerto lee latencias, contadores y estado de dependencias |
| 16 | Acciones del agente (eventos, correos, gastos) | Modelo ↔ BD/Gmail/n8n | **R**epudiation | Métricas por herramienta (`tool_latency_ms.*`), log de llamadas con argumentos truncados, `automation_runs` idempotente por `execution_id`, guardias de honestidad que impiden atribuir acciones falsas | Parcial | No hay registro de auditoría inmutable ni firma de las acciones; el log es editable por quien tenga acceso al host |
| 17 | Página/WS de llamada de voz | Navegador ↔ servidor de voz | **S**poofing | `VOICE_CALL_TOKEN` con `hmac.compare_digest` en cabecera o primer mensaje del WebSocket; CORS restringido a `WEB_ALLOWED_ORIGINS` | Parcial | **Sin token configurado el servidor queda abierto** (compatibilidad documentada): en una red local cualquiera podría escuchar la llamada |
| 18 | Fichero `data/backup.trigger` | Contenedor ↔ systemd del host | **E**levation · **D**oS | El contenedor sólo escribe un fichero en `/data` (sin `docker.sock` ni SSH); deduplicación (5 min) y antigüedad máxima (10 min); `flock` evita ejecuciones solapadas; el servicio borra el trigger al empezar | Parcial | Cualquier escritura de fichero en `data/` puede disparar el backup del host (coste de recursos, no de confidencialidad); ver [ADR-006](adr/006-file-trigger-backup.md) |

## 4. Resumen de lo que queda abierto

1. **Prompt-injection desde documentos indexados (fila 10)** — el caso más
   relevante: no hay separación de confianza entre el contenido ingerido y las
   instrucciones. Mitigación parcial indirecta: los modos `execute` de n8n
   exigen confirmación explícita.
2. **Cifrado en reposo de la bóveda y de SQLite (filas 3 y 4)** — fuera del
   alcance del código; depende del cifrado de disco del host.
3. **Rate limiting y límites de cuerpo en el panel web (filas 8 y 9)** — sólo
   existe para Telegram.
4. **Acceso al motor de IA desde toda la tailnet (fila 13)** — riesgo aceptado
   y documentado en `docs/SECURITY.md`.
5. **Servidor de voz sin token (fila 17)** y **`/metrics` sin autenticación
   (fila 15)** — dependen de que los puertos sigan en `127.0.0.1`.

## 5. Fuera de alcance

Tal y como fija `docs/SECURITY.md`: ataques en caliente con el sistema
arrancado, malware en el host, ingeniería social y análisis de tráfico de red
local. No se evalúa en esta v1 el abuso de cuota de la API de Telegram ni la
disponibilidad del nodo de inferencia (cubierto por `InfraWorker` y por el
modo degradado del orquestador).

---

*Revisar este documento al añadir endpoints, herramientas o canales nuevos.*
