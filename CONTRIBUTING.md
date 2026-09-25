# Contribuir a Rafita AVP

Gracias por el interés. Este es un proyecto personal compartido en abierto:
los PRs y los issues son bienvenidos.

## Entorno de desarrollo

```bash
git clone https://github.com/rufae/Rafita.git
cd Rafita
python3 scripts/setup_wizard.py      # configura .env y valida el proveedor IA
docker compose up -d                 # o con GPU: -f docker-compose.yml -f docker-compose.gpu.yml
```

Para trabajar sobre el código sin Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
pip install -r dev-requirements.txt
export TELEGRAM_TOKEN=dummy          # requerido por Settings
```

## Tests y calidad

```bash
ruff check agent/src --config pyproject.toml
ruff format --check agent/src --config pyproject.toml
mypy agent/src --config-file pyproject.toml
pytest agent/tests -q
pre-commit run --all-files
```

- Los tests que requieren Ollama (embeddings e integración) se saltan
  automáticamente si no está disponible; para ejecutarlos en local levanta
  Ollama y exporta `OLLAMA_HOST`.
- `agent/scripts/rag_eval.py` mide Recall@k/MRR contra el dataset de
  `agent/tests/rag_eval/` y `agent/scripts/tool_calling_eval.py` mide la
  fiabilidad de las tools con un modelo real.

## Estilo

- Python 3.11, líneas de 100 caracteres, formato con `ruff format`.
- Commits en español, imperativos y con alcance: `fix(rag): ...`,
  `feat(config): ...`, `test(...)`, `docs(...)`.
- Cada cambio relevante debe poder verificarse con un comando o test; evita
  afirmar que algo funciona sin evidencia reproducible.

## Flujo de PR

1. Abre un issue o comenta el existente describiendo el problema.
2. Crea una rama (`fix/...`, `feat/...`) y añade tests cuando aplique.
3. Asegúrate de que pasan lint, tipos y tests; el CI (`.github/workflows/ci.yml`)
   replica estos pasos más `pip-audit`, `gitleaks` y el build Docker.
4. Describe en el PR qué evidencia lo respalda (comando y salida).

## Seguridad

No incluyas secretos en issues, PRs ni tests. Para vulnerabilidades, sigue el
procedimiento descrito en [SECURITY.md](SECURITY.md).
