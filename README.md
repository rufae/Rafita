# Rafita AVP — Asistente Virtual Privado con Segundo Cerebro

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![CI](https://github.com/rufae/Rafita/actions/workflows/ci.yml/badge.svg)](https://github.com/rufae/Rafita/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Tu propio asistente de IA personal que convierte tu vault de Obsidian en un
segundo cerebro buscable y auto-organizado, 100% local y privado.

**Sin nube. Sin telemetría. Sin fugas de datos. Tú controlas todo.**

---

## Qué hace Rafita

### Conversación y herramientas
- **Chat con IA local** (Ollama, p. ej. `gemma4:12b`) por **Telegram**, **web**
  y **llamada de voz**, con el mismo cerebro en los tres canales.
- **46 herramientas** que el modelo decide invocar solo (sin enrutado por
  palabras clave): eventos, alertas, finanzas, contactos, correo, tareas,
  Google Calendar/Drive/Gmail/Tasks, búsqueda web, bóveda, CRM, grafo de
  conocimiento, n8n, copias de seguridad bajo demanda, estado de las
  automatizaciones, exportaciones RGPD…
- **Honestidad garantizada**: guardias deterministas impiden que el modelo
  diga «he creado el evento» si ninguna herramienta lo hizo.

### Segundo cerebro (RAG)
- **Indexa tu vault de Obsidian** semánticamente (ChromaDB + `bge-m3`) con
  chunking por encabezados y reindexado en vivo por watchdog.
- **Búsqueda híbrida con citas**: reranking léxico + entidades + embeddings
  (ideal para nombres propios) y respuestas con **citas `[S1]` y enlace
  `obsidian://`** a la nota de origen.
- **Grafo de conocimiento**: relaciones tipadas («Ana trabaja en Proyecto X»)
  con tools `add_relation`/`search_relations`/`delete_relation`.
- Aprendizaje de hechos (`remember_fact`), alias de contactos y conocimiento
  personal cifrado (Fernet).

### Voz
- **Llamada en tiempo real** (pestaña Llamada de la web): transcripción con
  Whisper (servicio `large-v3` en GPU con fallback local), VAD adaptativo
  anti-ruido, barge-in por voz sostenida, TTS por frases (Piper/Kokoro) y
  frases de espera si la respuesta tarda.
- **Notas de voz por Telegram** (STT + respuesta hablada) y **dictado**: lee
  direcciones de correo en voz alta («ejemplo punto ejemplo arroba gmail punto
  com») y las entiende en chat, voz y llamada.
- **Reuniones tipo NotebookLM**: graba micro/pestaña, transcribe, diariza
  hablantes y genera resumen ejecutivo con tareas.

### Correo y contactos
- **Redactar correos** (`draft_gmail`: borrador en Gmail o en la bóveda) y
  **enviarlos** (`send_gmail`), con destinatario por nombre (se resuelve con
  tus contactos) o correo dictado.
- **Buscar correos** (`search_gmail`) y **encontrar contactos** (`find_contact`)
  por nombre, alias («mi madre») o correo.

### Operación y privacidad
- **Backups verificados**: restic cifrado al USB + Google Drive, copia horaria
  de la BD, restore-drill mensual y alertas si el backup falla. Copia
  completa **bajo demanda** con `/backup` o «hazme un backup ahora».
- **Observabilidad**: métricas de latencia por herramienta y tokens LLM en
  `/metrics`, y un vigilante de infraestructura (disco, IA, RAG, backup) que
  avisa por Telegram solo cuando algo se degrada.
- **RGPD**: `export_my_data` / `delete_my_data` y endpoints `/api/gdpr/*`.
- **Secretos file-based** (Docker secrets), webhooks HMAC, bot privado por
  whitelist y cifrado fail-closed de credenciales.
- **Automatizaciones n8n** (briefing matutino, inbox zero, sync Google,
  informe semanal, radar de IA, CRM…) con reintentos, timeouts y fallo
  controlado; cada ejecución se informa a Rafita y puedes preguntarle
  «¿qué automatizaciones han fallado esta semana?». Ver
  [n8n/README.md](n8n/README.md).

## Filosofía

Rafita es un **Asistente Virtual Privado**. Cada instalación es independiente:
un solo usuario, su propio vault, sus propios modelos, sus propias
credenciales. Nada se comparte entre instalaciones. Nada sale de tu máquina.

Esto no es un SaaS. Es una herramienta que instalas y posees.

## Arquitectura

Despliegue típico en dos nodos con GPU opcional (también funciona todo en una
máquina):

```
Tu Telegram/Web/Voz ──→ rafita-agent-core ──red privada──→ ollama (GPU torre o CPU Dell)
                            │        │        │
                       SQLite   ChromaDB  Obsidian vault
                      (memoria)  (RAG)    (2º cerebro)
                            │
                            └── backup diario (restic cifrado) → USB (+ Drive)
```

- **Nodo de aplicación** (HP u otro): agente, base vectorial, bóveda, web.
- **Nodo de IA** (torre con GPU o Dell): solo pesos de modelos; sin datos de
  usuario. Conmutado automático GPU→CPU y `keep_alive` por backend.
- **Backup diario** solo si el USB está conectado, con aviso por Telegram si
  se omite o falla.
- Todo funciona también en una sola máquina (dos contenedores Docker).

## Requisitos

- Docker y Docker Compose
- 8 GB de RAM mínimo (16 GB recomendado para bge-m3 + gemma4:12b)
- GPU NVIDIA opcional (acelera modelos grandes como gemma4:12b)
- Un bot de Telegram (gratis, se crea con @BotFather)
- Obsidian (opcional — para editar el vault con interfaz gráfica)

[Ver docs/INSTALL.md](docs/INSTALL.md) para instrucciones detalladas de instalación.

## Quickstart

```bash
git clone https://github.com/rufae/Rafita.git
cd Rafita
cp .env.example .env
# Edita .env: pon tu TELEGRAM_TOKEN de @BotFather
docker compose up -d
# Abre Telegram, busca tu bot, escribe /start
```

`/start` muestra un **checklist de estado real** de la instalación (IA, Google,
backups, voz, bóveda…) con el siguiente paso de cada pendiente.

## Comandos principales

| Comando | Descripción |
|---|---|
| `/demo` | Recorrido guiado por las capacidades reales (ideal para presentaciones) |
| `/ayuda` | Todos los comandos disponibles |
| `/chat <mensaje>` | Hablar con Rafita. Invoca herramientas automáticamente |
| `/evento <fecha> <título>` | Crear evento en la agenda |
| `/eventos` | Listar eventos próximos |
| `/alerta <mensaje>` | Crear una alerta |
| `/gasto <cantidad> <cat>` | Registrar un gasto (también **por foto** de ticket) |
| `/finanzas` | Resumen financiero del mes (tabla) |
| `/cerebro` | Estadísticas del segundo cerebro |
| `/resumen` | Resumen IA del contenido del vault |
| `/recordar [tema]` | Guardar la conversación como nota Zettelkasten |
| `/guardar_clave <srv> <val>` | Guardar API key cifrada (Fernet) |
| `/ubicacion <ciudad>` | Fijar tu ciudad (tiempo y avisos CAP) |
| `/backup` | Lanzar una copia de seguridad completa del sistema ahora |
| `/backup_zip` | Generar respaldo ZIP de datos en el chat |
| `/setup_google` | Conectar Google (Calendar, Drive, Gmail, Contactos…) |
| `/sync_google` | Copiar Google al segundo cerebro |
| `/status` | Panel de control completo del sistema |

En el **chat libre** (Telegram o web) puedes escribir cualquier cosa y el
modelo decide qué herramientas usar: «busca en mis notas qué sabes de Ana»,
«redacta un correo para…», «qué eventos tengo la próxima semana», «manda un
mensaje a mamá»…

## Modelos IA

Rafita detecta tu hardware automáticamente y recomienda el perfil óptimo:

| Perfil | Chat | Embeddings | Visión | Hardware |
|---|---|---|---|---|
| gpu-high | gemma4:12b | bge-m3 (1024d) | llava:7b | GPU ≥10GB VRAM |
| cpu-mid | qwen2.5:7b | bge-m3 (1024d) | llava:7b | 16GB+ RAM |
| cpu-low | qwen2.5:3b | nomic-embed-text (768d) | moondream | 8GB RAM |

También puedes configurar los modelos manualmente en `.env`.

## Web propia (chat, baúl, reuniones y llamada)

Además de Telegram, Rafita sirve una **web propia (PWA instalable)** desde el
gateway:

- **Chat** con las mismas herramientas (incluidos los comandos `/demo` y
  `/ayuda`), **Baúl** para gestionar la bóveda, **Reuniones** (grabar y
  transcribir) y **Llamada** de voz en tiempo real.
- Login por **email y contraseña** (JWT) y, opcionalmente, **Sign in with
  Google**.
- **Notificaciones push** (PWA): el botón «Avisos» de la cabecera las
  activa; el briefing diario y las alertas de automatizaciones llegan también
  al móvil (claves VAPID con `scripts/generate_vapid_keys.py`).
- **La llamada con micrófono requiere HTTPS o localhost** (restricción de los
  navegadores): en móvil usa la URL segura de Tailscale o activa SSL en el
  proxy. Ver [docs/web.md](docs/web.md) para puertos, proxy y configuración.

## Google es opcional

Rafita funciona **sin cuenta de Google**: el calendario, las tareas y los
ficheros viven en local (base de datos + bóveda Obsidian) y las mismas
herramientas operan sobre ellos. Si conectas tu cuenta (`/setup_google` o el
login con Google de la web), las herramientas pasan a usar Google Calendar,
Tasks, Drive, Gmail y Contactos sin cambiar nada más; `/sync_google` copia los
datos al segundo cerebro.

## Limitaciones conocidas

- **Relevancia RAG en español con bge-m3**: medida con el dataset de evaluación
  propio (36 casos) en hardware con GPU: recall@3 = 1.0, MRR@5 = 0.98, umbral
  calibrado 0.49 con 0 falsos positivos (ver [ADR-003](docs/adr/003-model-selection.md)).
  Pendiente validar con bóvedas reales y más negativos.
- **Fiabilidad de tools con gemma4:12b**: suite de 21 tools (2 intentos) →
  **46/46 (100%)** con el thinking desactivado (`reasoning_effort=none`).
- **Watchdog en Docker Desktop Windows**: `inotify` no propaga eventos a
  través de bind mounts. En Linux nativo funciona correctamente.
- **gemma4:12b requiere GPU**: no cabe en 16GB RAM en CPU-only. El sistema
  degrada automáticamente a qwen2.5:7b si no detecta GPU.

## Desarrollo

```bash
# Instalar dependencias de desarrollo
pip install -r dev-requirements.txt

# Lint + type check
ruff check agent/src --config pyproject.toml
mypy agent/src --config-file pyproject.toml

# Tests (≈1558, sin paralelo)
pytest agent/tests -v

# Todos los checks (pre-commit)
pre-commit run --all-files
```

## Seguridad

Ver [docs/SECURITY.md](docs/SECURITY.md) para el modelo de amenaza completo,
recomendaciones de cifrado en reposo y procedimiento de rotación de
credenciales. Los secretos también pueden servirse desde ficheros
([docs/secrets.md](docs/secrets.md)).

## Documentación

| Documento | Contenido |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Instalación detallada |
| [docs/web.md](docs/web.md) | Web, llamada, proxy y HTTPS |
| [docs/secrets.md](docs/secrets.md) | Secretos file-based (Docker secrets) |
| [docs/SECURITY.md](docs/SECURITY.md) | Seguridad y modelo de amenaza |
| [n8n/README.md](n8n/README.md) | Automatizaciones n8n (qué hacen y cuándo) |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Cambios por versión |

## Contribuir

- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md): entorno de desarrollo, tests y estilo.

## Licencia

AGPL-3.0 — Eres libre de usar, modificar y redistribuir Rafita, siempre que
compartas los cambios bajo la misma licencia. Si lo usas como servicio (SaaS),
debes publicar el código fuente.

## Soporte

Este es un proyecto personal compartido en abierto. **Uso bajo tu
responsabilidad, sin garantía de soporte**. Si encuentras un bug o tienes una
idea, abre un issue. Si quieres contribuir, los PRs son bienvenidos.
