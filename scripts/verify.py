#!/usr/bin/env python3
"""
Script de verificación del proyecto Rafita Agent Core.
Valida imports, configuración, y conectividad básica sin
iniciar el bot de Telegram ni requerir Ollama.
"""

import sys
import os
import importlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_SRC = PROJECT_ROOT / "agent" / "src"

if str(AGENT_SRC.parent) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC.parent))


def check_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        print(f"  [OK] {module_name}")
        return True
    except ImportError as e:
        print(f"  [FAIL] {module_name}: {e}")
        return False


def check_file_exists(rel_path: str) -> bool:
    full = PROJECT_ROOT / rel_path
    exists = full.exists()
    status = "[OK]" if exists else "[MISSING]"
    print(f"  {status} {rel_path}")
    return exists


def check_env_file() -> bool:
    env_path = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"

    if not env_example.exists():
        print("  [MISSING] .env.example")
        return False

    if not env_path.exists():
        print("  [WARN] .env no encontrado. Crea uno desde .env.example")
        return True

    missing_vars = []
    with open(env_example, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                var_name = line.split("=")[0].strip()
                if var_name not in os.environ:
                    with open(env_path, "r", encoding="utf-8") as ef:
                        env_content = ef.read()
                    if var_name not in env_content:
                        missing_vars.append(var_name)

    if missing_vars:
        print(f"  [WARN] Variables faltantes en .env: {', '.join(missing_vars)}")

    print("  [OK] .env encontrado")
    return True


def check_directory_structure() -> bool:
    required_dirs = [
        "agent/src",
        "agent/src/handlers",
        "agent/src/models",
        "data/db",
        "data/excels",
        "data/exports",
        "data/logs",
    ]
    all_ok = True
    for d in required_dirs:
        full = PROJECT_ROOT / d
        if full.exists():
            print(f"  [OK] {d}/")
        else:
            print(f"  [MISSING] {d}/")
            all_ok = False
    return all_ok


def check_config() -> bool:
    try:
        os.environ.setdefault("TELEGRAM_TOKEN", "test_token_123")
        os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
        from src.config import settings

        assert settings.telegram_token == "test_token_123"
        assert settings.ollama_host == "http://localhost:11434"
        print(f"  [OK] Config loaded: model={settings.ollama_model}")
        return True
    except Exception as e:
        print(f"  [FAIL] Config validation: {e}")
        return False


def check_schema_models() -> bool:
    try:
        from src.models.schemas import (
            ChatMessage,
            Event,
            Alert,
            FinanceRecord,
            FinanceSummary,
            ExportRequest,
            BotCommand,
            MessageRole,
            FinanceCategory,
            COMMANDS_REGISTRY,
        )

        assert len(COMMANDS_REGISTRY) == 12
        msg = ChatMessage(chat_id=123, role=MessageRole.user, content="test")
        assert msg.role.value == "user"
        print(f"  [OK] Schemas: {len(COMMANDS_REGISTRY)} commands registered")
        return True
    except Exception as e:
        print(f"  [FAIL] Schema validation: {e}")
        return False


def main() -> int:
    print("=" * 55)
    print("  RAFITA AGENT CORE - VERIFICACIÓN")
    print("=" * 55)

    errors = 0

    print("\n--- Archivos del proyecto ---")
    for f in [
        "docker-compose.yml",
        ".env.example",
        "agent/Dockerfile",
        "agent/requirements.txt",
        "agent/src/__init__.py",
        "agent/src/main.py",
        "agent/src/bot.py",
        "agent/src/config.py",
        "agent/src/database.py",
        "agent/src/logger.py",
        "agent/src/ollama_client.py",
        "agent/src/handlers/__init__.py",
        "agent/src/handlers/chat.py",
        "agent/src/handlers/finance.py",
        "agent/src/handlers/admin.py",
        "agent/src/models/__init__.py",
        "agent/src/models/schemas.py",
    ]:
        if not check_file_exists(f):
            errors += 1

    print("\n--- Directorios ---")
    if not check_directory_structure():
        errors += 1

    print("\n--- Variables de entorno ---")
    if not check_env_file():
        errors += 1

    print("\n--- Imports de bibliotecas ---")
    libs = [
        "telegram",
        "telegram.ext",
        "openai",
        "pandas",
        "openpyxl",
        "dotenv",
        "aiosqlite",
        "aiofiles",
        "httpx",
        "pydantic",
        "pydantic_settings",
        "matplotlib",
        "tabulate",
    ]
    for lib in libs:
        if not check_import(lib):
            errors += 1

    print("\n--- Módulos del proyecto ---")
    modules = [
        "src.config",
        "src.logger",
        "src.models.schemas",
        "src.database",
        "src.ollama_client",
        "src.bot",
        "src.handlers.chat",
        "src.handlers.finance",
        "src.handlers.admin",
        "src.main",
    ]
    for mod in modules:
        if not check_import(mod):
            errors += 1

    print("\n--- Validación de Config ---")
    if not check_config():
        errors += 1

    print("\n--- Validación de Schemas ---")
    if not check_schema_models():
        errors += 1

    print("\n" + "=" * 55)
    if errors == 0:
        print("  RESULTADO: VERIFICACIÓN COMPLETA [OK]")
    else:
        print(f"  RESULTADO: {errors} ERROR(ES) ENCONTRADO(S)")
    print("=" * 55)

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
