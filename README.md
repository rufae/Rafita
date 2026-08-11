# Rafita AVP — Asistente Virtual Privado con Segundo Cerebro

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![CI](https://github.com/user/rafai/actions/workflows/ci.yml/badge.svg)](https://github.com/user/rafai/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Tu propio asistente de IA personal que convierte tu vault de Obsidian en un
segundo cerebro buscable y auto-organizado, 100% local y privado.

**Sin nube. Sin telemetría. Sin fugas de datos. Tú controlas todo.**

---

## Qué hace Rafita

- **Habla contigo por Telegram** usando modelos locales (Ollama) — sin enviar tus mensajes a OpenAI ni a nadie
- **Indexa tu vault de Obsidian** semánticamente (RAG con embeddings) — busca en tus notas como si las entendiera
- **Aprende proactivamente**: detecta ideas, decisiones y datos personales en la conversación y los guarda solo en tu segundo cerebro
- **Gestiona finanzas, agenda, recordatorios y credenciales** desde Telegram
- **Procesa archivos** (PDF, DOCX, imágenes) — extrae texto con IA, los clasifica y los indexa
- **Funciona offline**: si apagas el PC, al encender hace catch-up de todos los mensajes pendientes
- **Detecta tu hardware** automáticamente y elige los modelos óptimos (CPU/GPU)

## Filosofía

Rafita es un **Asistente Virtual Privado**. Cada instalación es independiente:
un solo usuario, su propio vault, sus propios modelos, sus propias
credenciales. Nada se comparte entre instalaciones. Nada sale de tu máquina.

Esto no es un SaaS. Es una herramienta que instalas y posees.

## Arquitectura

```
Tu Telegram ──→ rafita-agent-core (Python) ──→ ollama (modelos locales)
                     │        │        │
                SQLite   ChromaDB  Obsidian vault
              (memoria)  (RAG)    (2º cerebro)
```

Dos contenedores Docker. Sin bases de datos externas. Sin Redis. Sin colas.
Ejecutable en un portátil o en un servidor doméstico.

## Requisitos

- Docker y Docker Compose
- 8 GB de RAM mínimo (16 GB recomendado para bge-m3 + qwen2.5:7b)
- GPU NVIDIA opcional (acelera modelos grandes como gemma4:12b)
- Un bot de Telegram (gratis, se crea con @BotFather)
- Obsidian (opcional — para editar el vault con interfaz gráfica)

[Ver INSTALL.md](INSTALL.md) para instrucciones detalladas de instalación.

## Quickstart

```bash
git clone https://github.com/user/rafai.git
cd rafai
cp .env.example .env
# Edita .env: pon tu TELEGRAM_TOKEN de @BotFather
docker compose up -d
# Abre Telegram, busca tu bot, escribe /start
```

## Comandos principales

| Comando | Descripción |
|---|---|
| `/chat <mensaje>` | Hablar con Rafita. Invoca herramientas automáticamente |
| `/cerebro` | Estadísticas del segundo cerebro |
| `/resumen` | Resumen IA del contenido del vault |
| `/escanear [fecha]` | Escanea mensajes pendientes de Telegram |
| `/recordar [tema]` | Guarda la conversación como nota Zettelkasten |
| `/gasto <cantidad> <cat>` | Registrar un gasto |
| `/finanzas` | Resumen financiero del mes |
| `/evento <título> <fecha>` | Crear evento en calendario |
| `/guardar_clave <srv> <val>` | Guardar API key cifrada (AES-256) |
| `/status` | Panel de control del sistema |

[Ver documentación completa](#) para todos los comandos.

## Modelos IA

Rafita detecta tu hardware automáticamente y recomienda el perfil óptimo:

| Perfil | Chat | Embeddings | Visión | Hardware |
|---|---|---|---|---|
| gpu-high | gemma4:12b | bge-m3 (1024d) | llava:7b | GPU ≥10GB VRAM |
| cpu-mid | qwen2.5:7b | bge-m3 (1024d) | llava:7b | 16GB+ RAM |
| cpu-low | qwen2.5:3b | nomic-embed-text (768d) | moondream | 8GB RAM |

También puedes configurar los modelos manualmente en `.env`.

## Limitaciones conocidas

- **Relevancia RAG >60% en español con bge-m3**: pendiente de validar en hardware con GPU (ver [ADR-003](docs/adr/003-model-selection.md)). La métrica actual en test controlado es prometedora (~68%) pero no conclusiva en CPU.
- **Watchdog en Docker Desktop Windows**: `inotify` no propaga eventos a través de bind mounts. El watcher no funciona en este entorno. En Linux nativo funciona correctamente.
- **gemma4:12b requiere GPU**: no cabe en 16GB RAM en CPU-only. El sistema degrada automáticamente a qwen2.5:7b si no detecta GPU.

## Desarrollo

```bash
# Instalar dependencias de desarrollo
pip install -r dev-requirements.txt

# Lint + type check
ruff check agent/src --config pyproject.toml
mypy agent/src --config-file pyproject.toml

# Tests
pytest agent/tests -v

# Todos los checks (pre-commit)
pre-commit run --all-files
```

## Seguridad

Ver [SECURITY.md](SECURITY.md) para el modelo de amenaza completo, recomendaciones
de cifrado en reposo y procedimiento de rotación de credenciales.

## Licencia

AGPL-3.0 — Eres libre de usar, modificar y redistribuir Rafita, siempre que
compartas los cambios bajo la misma licencia. Si lo usas como servicio (SaaS),
debes publicar el código fuente.

## Soporte

Este es un proyecto personal compartido en abierto. **Uso bajo tu
responsabilidad, sin garantía de soporte**. Si encuentras un bug o tienes una
idea, abre un issue. Si quieres contribuir, los PRs son bienvenidos.
