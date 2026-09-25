#!/usr/bin/env python3
"""Guided installation wizard for Rafita AVP (task 2.6).

Cross-platform and stdlib-only: prepares/validates `.env`, lets the user pick
the AI provider and model, tests the connection and validates the vault path
without editing code.

Usage:
    python3 scripts/setup_wizard.py                 # interactive
    python3 scripts/setup_wizard.py --non-interactive \
        --token 123:abc --provider ollama --model gemma4:12b \
        --embedding-model bge-m3 --base-url http://localhost:11434 \
        --vault ./mi_boveda_obsidian
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Update keys in a .env file preserving comments/order; append missing keys."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                output.append("%s=%s" % (key, pending.pop(key)))
                continue
        output.append(line)
    for key, value in pending.items():
        output.append("%s=%s" % (key, value))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def validate_vault(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, "no se pudo crear el vault %s: %s" % (path, exc)
    if not path.is_dir():
        return False, "%s no es un directorio" % path
    return True, "vault OK (%s)" % path


def _model_present(names: set[str], model: str) -> bool:
    return any(name == model or name.startswith(model + ":") for name in names)


def check_ollama(
    base_url: str, model: str, embedding_model: str, timeout: float = 5.0
) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - user-facing message
        return False, "no se pudo conectar a %s: %s" % (url, exc)
    names = {entry.get("name", "") for entry in data.get("models", [])}
    missing = [
        candidate
        for candidate in (model, embedding_model)
        if candidate and not _model_present(names, candidate)
    ]
    if missing:
        return False, "faltan modelos en Ollama: %s (ejecuta: ollama pull %s)" % (
            ", ".join(missing),
            missing[0],
        )
    return True, "Ollama OK (%d modelos disponibles)" % len(names)


def check_openai(base_url: str, api_key: str, timeout: float = 10.0) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer %s" % (api_key or "dummy")}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "credenciales rechazadas por %s (HTTP %d)" % (url, exc.code)
        return False, "no se pudo conectar a %s: HTTP %d" % (url, exc.code)
    except Exception as exc:  # noqa: BLE001 - user-facing message
        return False, "no se pudo conectar a %s: %s" % (url, exc)
    return True, "endpoint compatible OpenAI OK"


def build_updates(args: argparse.Namespace, current: dict[str, str]) -> dict[str, str]:
    provider = (args.provider or current.get("AI_PROVIDER") or "ollama").strip().lower()
    updates = {
        "TELEGRAM_TOKEN": args.token or current.get("TELEGRAM_TOKEN", ""),
        "AI_PROVIDER": provider,
    }
    if provider == "openai":
        updates["OPENAI_BASE_URL"] = args.base_url or current.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        updates["OPENAI_MODEL"] = args.model or current.get("OPENAI_MODEL", "gpt-4o-mini")
        if args.api_key or current.get("OPENAI_API_KEY"):
            updates["OPENAI_API_KEY"] = args.api_key or current.get("OPENAI_API_KEY", "")
        if args.embedding_model:
            updates["OPENAI_EMBEDDING_MODEL"] = args.embedding_model
    else:
        updates["OLLAMA_MODEL"] = args.model or current.get("OLLAMA_MODEL", "qwen2.5:7b")
        updates["EMBEDDING_MODEL"] = args.embedding_model or current.get(
            "EMBEDDING_MODEL", "bge-m3"
        )
        if args.embedding_model == "bge-m3" or current.get("EMBEDDING_DIM") == "1024":
            updates["EMBEDDING_DIM"] = "1024"
    return updates


def _prompt(label: str, default: str) -> str:
    answer = input("%s [%s]: " % (label, default)).strip()
    return answer or default


def main() -> int:
    parser = argparse.ArgumentParser(description="Rafita AVP installation wizard")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--token", default="")
    parser.add_argument("--provider", choices=["ollama", "openai"], default=None)
    parser.add_argument("--model", default="")
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--vault", default="")
    parser.add_argument("--skip-connection-test", action="store_true")
    args = parser.parse_args()

    if not args.env_file.exists():
        if not ENV_EXAMPLE.exists():
            print("ERROR: falta %s y %s" % (args.env_file, ENV_EXAMPLE))
            return 1
        shutil.copyfile(ENV_EXAMPLE, args.env_file)
        print("[1/5] .env creado desde .env.example")

    current = parse_env(args.env_file)

    if not args.non_interactive:
        args.token = _prompt(
            "Token de Telegram (@BotFather)", args.token or current.get("TELEGRAM_TOKEN", "")
        )
        args.provider = _prompt(
            "Proveedor de IA (ollama/openai)", args.provider or current.get("AI_PROVIDER", "ollama")
        )
        default_model = (
            current.get("OPENAI_MODEL", "gpt-4o-mini")
            if args.provider == "openai"
            else current.get("OLLAMA_MODEL", "qwen2.5:7b")
        )
        args.model = _prompt("Modelo de chat", args.model or default_model)
        args.embedding_model = _prompt(
            "Modelo de embeddings",
            args.embedding_model or current.get("EMBEDDING_MODEL", "bge-m3"),
        )
        if args.provider == "openai":
            args.base_url = _prompt(
                "Base URL OpenAI-compatible",
                args.base_url or current.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
            args.api_key = _prompt("API key", args.api_key or current.get("OPENAI_API_KEY", ""))
        args.vault = _prompt(
            "Carpeta del vault Obsidian (host)", args.vault or "mi_boveda_obsidian"
        )

    if not args.token:
        print("ERROR: falta TELEGRAM_TOKEN")
        return 1

    updates = build_updates(args, current)
    print(
        "[2/5] Configuracion: provider=%s model=%s embeddings=%s"
        % (
            updates["AI_PROVIDER"],
            updates.get("OLLAMA_MODEL") or updates.get("OPENAI_MODEL"),
            updates.get("EMBEDDING_MODEL") or updates.get("OPENAI_EMBEDDING_MODEL", "default"),
        )
    )

    vault = Path(args.vault).expanduser() if args.vault else ROOT / "mi_boveda_obsidian"
    ok, message = validate_vault(vault)
    print("[3/5] %s" % message)
    if not ok:
        return 1

    if args.skip_connection_test:
        print("[4/5] Test de conexion omitido")
    else:
        if updates["AI_PROVIDER"] == "openai":
            base_url = updates.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            ok, message = check_openai(base_url, updates.get("OPENAI_API_KEY", ""))
        else:
            base_url = (
                args.base_url or current.get("OLLAMA_BASE_URL", "") or "http://localhost:11434"
            )
            ok, message = check_ollama(
                base_url,
                updates.get("OLLAMA_MODEL", ""),
                updates.get("EMBEDDING_MODEL", ""),
            )
        print("[4/5] %s" % message)
        if not ok:
            print("Corrige el proveedor de IA y vuelve a ejecutar el wizard.")
            return 1

    upsert_env(args.env_file, updates)
    print("[5/5] .env actualizado: %s" % args.env_file)
    print()
    print("Siguiente paso:")
    print("  docker compose up -d                # CPU")
    print("  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d   # GPU NVIDIA")
    print("  docker compose logs -f rafita-agent-core")
    return 0


if __name__ == "__main__":
    sys.exit(main())
