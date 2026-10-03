"""Estado compartido de los scripts de operación (mejora 2 y 3).

`deploy/hp/backup/backup.sh` y el restore-drill escriben ficheros de estado
JSON en `data/` (`backup-status.json`, `restore-drill-status.json`). El
informe semanal, las alertas de infraestructura y el onboarding los leen de
aquí para no duplicar la lógica de parseo.
"""

import json
from typing import Any

from src.config import settings


def read_status_file(name: str) -> dict[str, Any]:
    from pathlib import Path

    path = Path(settings.data_dir) / name
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return {}
