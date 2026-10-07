"""Skills: procedimientos en Markdown que el agente carga bajo demanda.

Formato inspirado en AgentSkills (OpenClaw/Hermes): ficheros `skills/*.md`
con `description` en frontmatter y un cuerpo de instrucciones que el modelo
sigue con sus herramientas normales. Solo texto: una skill NO ejecuta
codigo. Sin descargas ni marketplace: la cadena de suministro es el riesgo
(leccion ClawHavoc 2026: skills de terceros con codigo malicioso).

Dos raices:
- `agent/skills/` — skills versionadas con el agente (depuradas a mano).
- `<boveda>/skills/` — skills del usuario o creadas por el learning loop.
  Si el nombre coincide, gana la de la boveda (mas viva).
"""

from pathlib import Path
from typing import Any

from src.logger import logger
from src.utils.vault_indexer import VAULT_PATH, parse_frontmatter

REPO_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def _vault_skills_dir() -> Path:
    return VAULT_PATH / "skills"


def _parse_skill(path: Path) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("Skill %s ilegible: %s", path.name, e)
        return None
    meta, body = parse_frontmatter(content)
    descripcion = str(meta.get("description") or "").strip()
    if not descripcion:
        primera = body.strip().splitlines()[0] if body.strip() else ""
        descripcion = primera.lstrip("# ").strip()[:140]
    return {
        "name": path.stem,
        "description": descripcion,
        "body": body.strip(),
        "source": str(path.parent),
    }


def list_skills() -> list[dict[str, Any]]:
    """Todas las skills (boveda pisa repo si el nombre coincide)."""
    por_nombre: dict[str, dict[str, Any]] = {}
    for root in (REPO_SKILLS_DIR, _vault_skills_dir()):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            skill = _parse_skill(path)
            if skill:
                por_nombre[skill["name"]] = skill
    return [por_nombre[n] for n in sorted(por_nombre)]


def get_skill(name: str) -> dict[str, Any] | None:
    """Devuelve una skill por nombre (el nombre es el stem del fichero)."""
    limpio = Path(str(name or "").strip()).stem
    if not limpio:
        return None
    for root in (_vault_skills_dir(), REPO_SKILLS_DIR):
        path = root / ("%s.md" % limpio)
        if path.is_file():
            return _parse_skill(path)
    return None


def save_skill(name: str, description: str, body: str) -> Path:
    """Guarda/actualiza una skill en la boveda (la usa el learning loop)."""
    limpio = Path(str(name or "").strip()).stem
    if not limpio:
        raise ValueError("Skill sin nombre")
    carpeta = _vault_skills_dir()
    carpeta.mkdir(parents=True, exist_ok=True)
    path = carpeta / ("%s.md" % limpio)
    content = (
        "---\n"
        + "description: %s\n" % description.replace("\n", " ").strip()
        + "---\n\n"
        + body.strip()
        + "\n"
    )
    path.write_text(content, encoding="utf-8")
    logger.info("Skill guardada: %s (%d bytes)", path.name, len(content))
    return path


def skills_catalog_prompt() -> str:
    """Fragmento de system prompt con el catalogo de skills (sin cuerpo)."""
    skills = list_skills()
    if not skills:
        return ""
    linea = "; ".join("%s — %s" % (s["name"], s["description"][:80]) for s in skills)
    return (
        "SKILL_RULE: tienes skills (procedimientos guardados en Markdown). Si una "
        "encaja con la peticion del usuario, usala: listar_skills muestra todas y "
        "cargar_skill(nombre) devuelve el cuerpo completo para seguirlo paso a paso. "
        "Si no hay skill adecuada, responde con normalidad (no inventes skills).\n"
        "Skills disponibles: %s\n" % linea
    )
