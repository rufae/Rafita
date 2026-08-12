# Changelog

Todos los cambios importantes en Rafita AVP se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [0.1.0] - 2026-08-12

### Añadido

#### Fase 0 — Desbloqueo inicial
- Fix crítico: OllamaEmbeddingFunction ahora lanza excepción en fallos en vez de guardar vectores-cero
- Procesamiento de chunks uno a uno con 3 reintentos y backoff exponencial
- Timeout de embeddings aumentado a 600s para hardware modesto
- Fórmula de relevancia corregida: `max(0, 1.0 - distance/2.0)` para métrica L2
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

- **F0.5**: Relevancia RAG >60% en español con bge-m3 no validada en producción (requiere GPU)
- **F9.5/F9.6**: Prueba real de voz con RAG no pasa en CPU (qwen2.5:7b no invoca tools de forma fiable ~50% de las veces)
- **Watchdog en Docker Desktop Windows**: inotify no propaga eventos a través de bind mounts
- **gemma4:12b requiere GPU**: no cabe en 16GB RAM en CPU-only

### Seguridad

- Ver [SECURITY.md](SECURITY.md) para modelo de amenaza completo
- Cifrado de disco recomendado (BitLocker/LUKS/FileVault)
- Cifrado de credenciales con AES-256 Fernet
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
