# Changelog

Todos los cambios importantes en Rafita AVP se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

Ronda de estabilización tras la auditoría externa del 2026-09-24 (commits
`5ce1cac`..`83a337b`). Todas las tareas tienen evidencia ejecutada en `plan.md`.

### Corregido
- Cifrado de credenciales fail-closed y persistente: la clave se guarda en el
  mismo `.env` que lee `Settings`, sin fallback a texto plano y sin clave
  efímera (tarea 0.1).
- Path traversal del vault confinado con resolución real de ancestro
  (`utils/path_safety.resolve_within`), incluidos symlinks y prefijos hermanos
  (tarea 0.3).
- Webhook secret único por instancia; endpoints fail-closed (503 sin secreto,
  401 con firma inválida) y sin default compartido (tarea 0.4).
- `ADMIN_IDS` acepta CSV/JSON del `.env.example` sin edición extra (tarea 0.5).
- Health check real: `/ready` comprueba Ollama+modelo, Chroma y Telegram;
  `/health` queda como liveness (tarea 0.6).
- Reindexado RAG: la clave de borrado coincide con la de indexado
  (`note_path`), con compatibilidad para filas legadas (tarea 1.1).
- Tests de Fernet reescritos sin auto-captura del `AssertionError` (tarea 1.8).
- Dependencias: `pypdf 6.19.0`, `fastapi 0.141.1`, `starlette 1.7.0`; el gate de
  `pip-audit` es bloqueante con 3 excepciones documentadas de ChromaDB (0.2).
- Documentación alineada: URLs reales, descripción correcta de Fernet,
  puertos en loopback, vault montado, estado de Calendar (Fase 4).
- Modelos que razonan por defecto (gemma4): las llamadas de chat Ollama envían
  `reasoning_effort` (`OLLAMA_REASONING_EFFORT`, por defecto `none`) y el
  prewarm nativo desactiva `think`; antes el razonamiento consumía
  `LLM_MAX_TOKENS` y el contenido visible llegaba vacío. `OLLAMA_NUM_THREAD`
  hace configurable el número de hilos (antes `num_thread=8` fijo en visión) y
  `OPENAI_REASONING_EFFORT` permite lo mismo con `AI_PROVIDER=openai` sobre
  Ollama `/v1` (tarea 3.1).

### Añadido
- Evaluación RAG reproducible: 36 casos en español, `Recall@3 = 1.0`,
  `MRR@5 = 0.98` con bge-m3, umbral calibrado 0.49 (0 falsos positivos en el
  dataset) y respuesta `NO_ENCONTRADO` (tareas 1.2–1.5).
- Filtrado por tags en la query de Chroma con flags escalares (tarea 1.6).
- Suite de tool-calling con modelo real: 21 tools, 2 intentos por tool,
  equivalencias explícitas (tarea 1.7).
- `BrainMaintainer`: versionado git del vault con snapshots y revert real,
  carpetas protegidas configurables y desactivado por defecto (tarea 2.4).
- Taxonomía del vault, idioma, zona horaria, moneda y nombre configurables
  (tareas 2.1–2.2); `PERSIST_TO_BRAIN` bloquea escrituras en modo depuración
  (tarea 2.3).
- Adapters de proveedor de IA: Ollama local y cualquier endpoint
  OpenAI-compatible, seleccionable por configuración (tarea 2.5).
- Wizard de instalación multiplataforma (`scripts/setup_wizard.py`, tarea 2.6)
  y `CONTRIBUTING.md`.
- Test-guardia de ChromaDB embebido (`test_chromadb_embedded_only.py`): falla
  si se introduce modo servidor o `trust_remote_code`, convirtiendo en
  invariante verificable la mitigación de los avisos PYSEC-2026-3813/3814/3815
  (revisión Dependabot 2026-09-26; no existe versión corregida).
- Visión con el mismo modelo que chat (`gemma4:12b`): si
  `OLLAMA_VISION_MODEL == OLLAMA_MODEL` se omite el hot-swap (antes descargaba
  y recargaba los mismos pesos en cada imagen) y se mantiene `keep_alive=-1`.
  Validado con imagen real: `content='Rojo'` (2026-09-26).
- Arranque con el LLM caído (tarea 3.4): el agente ya no se bloquea en
  "Connecting to Ollama..." cuando el nodo de IA está apagado; arranca en modo
  degradado (Telegram y gateway activos, `/ready` 503) y se recupera solo al
  volver el backend. Health check de arranque con timeout corto y prewarm
  omitido si no hay backend.
- Backup diario de todo el homelab al USB RAFAEL (tarea 3.6): restic cifrado
  e incremental con retención 7d/4s/6m, solo si el USB está presente; cubre
  Rafita, BuenaTierra, Nextcloud, NPM, AdGuard, WireGuard, Portainer y la
  configuración, más snapshot diario de config del Dell. Aviso por Telegram y
  verificación de restore no destructiva (SQLite, Chroma, Fernet y Postgres).
  Incluye el renombrado seguro del usuario del HP a `server`.
- Logs estructurados y seguros (tarea 3.5): `LOG_FORMAT=json` opcional,
  redacción de credenciales en todos los handlers antes de escribir a disco,
  rotación acotada en fichero (10 MB×5 / 5 MB×3) y en Docker
  (`json-file` 10m×3), y comando remoto `/logs` (solo administradores) para
  consultar logs sin SSH. Arreglado `/status` con los estados de 3.3.
- Readiness real (tarea 3.3): `/ready` usa el `check_health()` genérico del
  proveedor de IA (`ok`/`degraded`/`unhealthy` según modelo disponible y
  cargado), añade check de vault (existencia/permisos) y agrega
  `ready`/`degraded`/`not_ready` con fallo rápido (<10 s con el nodo caído);
  `/health` queda como liveness. Sonda versionada `deploy/hp/ready_probe.py`
  y 5 escenarios de fallo verificados en el HP.

### Limitaciones conocidas (ronda de estabilización)
- La calidad RAG está medida sobre un vault de evaluación sintético; falta
  validarla con el vault personal real y más negativos.
- Tool-calling con `gemma4:12b`: la medición de 29/46 era efecto del
  `thinking` del modelo, activo por defecto. Con `reasoning_effort=none`
  (tareas 3.1/3.2) la suite completa da **46/46 (100%)** en el despliegue real
  (agente en HP → LLM en Dell). Ver README y `plan.md`.
- El despliegue continuo ya está desplegado (LLM en Dell, agente en HP por
  Tailscale; tareas 3.1/3.2); las pruebas de caos, logs y upgrade/rollback
  siguen pendientes (3.4–3.7).

## [0.1.0] - 2026-08-12

### Añadido

#### Fase 0 — Desbloqueo inicial
- Fix crítico: OllamaEmbeddingFunction ahora lanza excepción en fallos en vez de guardar vectores-cero
- Procesamiento de chunks uno a uno con 3 reintentos y backoff exponencial
- Timeout de embeddings aumentado a 600s para hardware modesto
- Fórmula de relevancia ajustada: `max(0, 1.0 - distance/2.0)` (la ronda de
  estabilización verificó después que con vectores unitarios equivale a la
  similitud coseno; ver `[Unreleased]`)
- Timeout de chat aumentado a 600s para CPU-only

#### Fase 1 — Calidad de código base
- Configuración de ruff (lint + format) con 400+ auto-fixes aplicados
- Configuración de mypy con overrides justificados para stubs incompletos
- Pre-commit hooks: ruff check, ruff format, mypy
- 34 tests unitarios: vault_indexer (12), vector_manager (13), chat_tools (8), fernet (4)
- pytest.ini con `pythonpath = ["agent"]` para resolución robusta de imports
- chat.py partido: TOOLS_DEFINITIONS extraído a chat_tools.py (580 líneas)

#### Fase 2 — Seguridad y privacidad
- SECURITY.md con modelo de amenaza completo
- Cifrado en reposo documentado: BitLocker/LUKS/FileVault recomendado
- Procedimiento de rotación de clave Fernet documentado
- pip-audit integrado: 63 vulnerabilidades iniciales → 8 (starlette pendiente)
- Auditoría de path traversal en webhook_server.py y files.py
- Fix de path traversal: `_safe_vault_subpath()` en files.py
- Puertos restringidos a localhost (127.0.0.1) en docker-compose.yml
- Filtrado de tags post-procesamiento en Python (ChromaDB 0.5.0 no soporta `$contains`)

#### Fase 3 — Observabilidad
- Correlation ID por conversación vía contextvars
- Métricas locales: latencia LLM, latencia embeddings, tasa de fallos de tools
- Endpoint `/metrics` en FastAPI para monitoreo
- Health monitor en background: alerta si tools fallan >10 veces o DB vacía

#### Fase 4 — Arquitectura
- ADR-001: Almacén vectorial (mantener ChromaDB, 0 servicios extra)
- ADR-002: Cola de trabajos (sin cola externa, asyncio inline)
- ADR-003: Modelos de IA (gemma4:12b GPU, qwen2.5:7b CPU, bge-m3 embeddings)
- Detección automática de hardware: GPU/CPU profiles
- 0 servicios nuevos añadidos al stack

#### Fase 5 — CI/CD
- GitHub Actions: lint + tests + security scan + Docker build
- Dockerfile multi-stage: builder + runtime, usuario no-root (rafita)
- Backup automatizado con verificación de integridad
- gitleaks para detección de secretos en el historial

#### Fase 6 — Documentación técnica
- ADR-003: Modelos de IA con evidencia empírica
- Runbook de incidentes: crash loop, backfill fallido, bot no responde, rotación Fernet, restauración backup

#### Fase 7 — Preparación para publicación
- Licencia AGPL-3.0 añadida
- README.md profesional con badges, arquitectura, quickstart, limitaciones conocidas
- INSTALL.md completo: perfiles hardware, 7 pasos, troubleshooting
- vault_ejemplo/ con 3 notas demo (sin datos reales)
- ASSISTANT_NAME configurable en .env
- Repo publicado en github.com/rufae/Rafita

#### Fase 8 — Pulido final
- CI verde en GitHub Actions (4/4 jobs passing)
- Tag v0.1.0 creado y pusheado
- Topics añadidos: self-hosted, obsidian, local-llm, ollama, rag, privacy, telegram-bot, second-brain, ai-assistant
- Secret scanning y Dependabot activados
- Issue #1: F0.5 pendiente (relevancia RAG >60% requiere GPU)
- CVEs resueltos: cryptography 50.0.0, python-dotenv 1.2.2

#### Fase 9 — Voz: unificación del cerebro
- Diagnóstico: voz usaba `llm.chat_stream_tokens()` directo sin tools ni RAG
- Orquestador compartido creado: `agent/src/core/orchestrator.py`
- Voz ahora consume el mismo orquestador que Telegram (mismo system prompt, mismas tools, mismo RAG)
- STT mejorado: faster-whisper modelo "base" (antes "tiny"), language="es" forzado
- WebSocket mejorado: orchestrator en background, cancelación de tarea LLM al colgar
- Test Fernet añadido: 4 tests (roundtrip, clave inválida, multi-valor UTF-8, token manipulado)
- SECURITY.md actualizado: sección "Privacidad en llamadas de voz"

### Limitaciones conocidas

- **F0.5**: Relevancia RAG >60% en español con bge-m3 no validada en producción (requiere GPU).
  → Actualizado en `[Unreleased]`: medida en GPU (Recall@3 1.0, MRR@5 0.98, umbral 0.49);
  queda validar con el vault personal real.
- **F9.5/F9.6**: Prueba real de voz con RAG no pasa en CPU (qwen2.5:7b no invoca tools de forma fiable ~50% de las veces).
  → Actualizado en `[Unreleased]`: suite de tool-calling con gemma4:12b en GPU (29/46 con equivalencias);
  7 tools no se invocan de forma fiable, mejora planificada para v0.2.0.
- **Watchdog en Docker Desktop Windows**: inotify no propaga eventos a través de bind mounts
- **gemma4:12b requiere GPU**: no cabe en 16GB RAM en CPU-only

### Seguridad

- Ver [SECURITY.md](SECURITY.md) para modelo de amenaza completo
- Cifrado de disco recomendado (BitLocker/LUKS/FileVault)
- Cifrado de credenciales con Fernet (AES-128-CBC + HMAC-SHA256)
- Privacidad en llamadas de voz: RAG puede recuperar datos sensibles sin vista previa

## [0.0.0] - 2026-08-10

### Añadido
- Versión inicial pre-pública (no publicada)
- Arquitectura base: Docker Compose con ollama-service + rafita-agent-core
- Bot de Telegram con comandos básicos
- Vault de Obsidian con estructura PARA+Zettelkasten
- Chunking semántico H2/H3
- ChromaDB para búsqueda vectorial
- Cifrado de credenciales con Fernet

[0.1.0]: https://github.com/rufae/Rafita/releases/tag/v0.1.0
[0.0.0]: https://github.com/rufae/Rafita/commits/v0.0.0
