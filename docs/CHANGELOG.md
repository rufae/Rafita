# Changelog

Todos los cambios importantes en Rafita AVP se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

### Añadido
- **Integración completa con Google** vía OAuth: Calendar, Drive, Sheets,
  Docs, Tasks, Gmail (lectura y envío) y Contactos, con errores humanizados y
  resolución de calendario configurable (`/calendario`, `/setup_google`).
- **`/sync_google`**: copia contactos, agenda y Drive al segundo cerebro local
  (notas en `Google/`) para consultar sin depender de la API.
- **Modo llamada por voz** (WebSocket + web): STT especulativo (transcribe
  mientras hablas), TTS por frases con caché del modelo Piper, barge-in por
  voz y botón, y UI con dos orbes animados según el nivel real de audio.
- **`trigger_n8n`**: ejecutar automatizaciones de n8n desde chat o voz, con
  flujos de ejemplo importables.
- **Briefing matutino** (agenda + correo + tiempo con open-meteo) y
  **recordatorios proactivos** de eventos y tareas.
- **Backup v2**: verificación `restic check` tras cada copia, restore-drill
  mensual automático, copia horaria de la base de datos y sincronización
  opcional a la nube con rclone.
- **Bot privado**: whitelist por `ADMIN_IDS` y rate limiting por usuario.
- **Seguridad de la voz**: token de acceso para la página/WS de llamadas y
  CORS configurable (por defecto solo localhost).
- **Calidad**: 1.169 tests con cobertura del 94% y candado en CI al 90%;
  `ruff`/`mypy` limpios; Dependabot para pip, GitHub Actions y Docker.

### Corregido
- Cifrado de credenciales fail-closed y persistente (nunca texto plano).
- Path traversal del vault confinado con resolución real de ancestro,
  incluidos symlinks y prefijos hermanos.
- Webhooks HMAC fail-closed: secreto único por instancia, 503 sin secreto y
  401 con firma inválida.
- Historial de chat estable (desempate por `id`) y búsqueda de contactos
  robusta ante variantes del STT («A A mamá» → «Aa Mama»), con alias
  aprendidos («mi madre es X»).
- Varios bugs de fechas/orden detectados por la campaña de tests (fin de mes,
  reindexado RAG, `search_google_drive`, reconexión con busy-spin).

### Seguridad
- Dependencias sin CVEs conocidas; 3 avisos de ChromaDB aceptados con
  mitigación documentada (uso embebido, sin servidor HTTP).
- Los secretos viven en `.env` (no versionado) y las credenciales OAuth se
  guardan cifradas.

## [0.2.0] - 2026-09-27
- Integración Google, modo llamada, sincronización con el segundo cerebro,
  contactos robustos, bot privado con rate limiting y campaña de calidad
  (tests + cobertura + CI).

## [0.1.0] - 2026-08-12
- Primera versión funcional: bot de Telegram, RAG con ChromaDB, bóveda
  Obsidian, finanzas, alertas y panel de control.

[Unreleased]: https://github.com/rufae/Rafita/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rufae/Rafita/releases/tag/v0.2.0
[0.1.0]: https://github.com/rufae/Rafita/releases/tag/v0.1.0
