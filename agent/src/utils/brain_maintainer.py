"""Local git versioning with safe revert for the Obsidian vault (task 2.4).

Disabled by default (`BRAIN_MAINTENANCE=false`). When enabled it takes periodic
snapshots (commits) of the vault and offers a real `revert()` that restores the
tracked files to a previous commit while optionally preserving the configured
protected folders.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from src.config import settings
from src.logger import logger
from src.vault_config import get_taxonomy

GIT_IDENTITY = (
    "-c",
    "user.name=Rafita BrainMaintainer",
    "-c",
    "user.email=brainmaintainer@localhost",
)


class BrainMaintainer:
    def __init__(self, vault_path: Path | None = None, interval_seconds: int | None = None):
        self.vault = vault_path or Path(settings.obsidian_vault_dir)
        self.interval = interval_seconds or settings.brain_maintenance_interval
        self._task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *GIT_IDENTITY, *args],
            cwd=str(self.vault),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.debug("git %s -> %s", " ".join(args), result.stderr.strip()[:300])
        return result

    def _write_gitignore(self) -> None:
        ignored = sorted(
            name for name in get_taxonomy().ignored_dirs if name not in {"Documentos_Indexados"}
        )
        lines = ["# Maintained by BrainMaintainer"]
        lines += [name + "/" for name in ignored if name]
        (self.vault / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def initialize(self) -> dict[str, Any]:
        """Create/validate the local git repository inside the vault."""

        def _init() -> dict[str, Any]:
            self.vault.mkdir(parents=True, exist_ok=True)
            if not (self.vault / ".git").exists():
                self._git("init", "-q")
                self._write_gitignore()
                logger.info("BrainMaintainer: git repository initialized at %s", self.vault)
                return {"status": "initialized", "vault": str(self.vault)}
            return {"status": "existing", "vault": str(self.vault)}

        return await asyncio.get_running_loop().run_in_executor(None, _init)

    async def snapshot(self, message: str = "brain snapshot") -> str | None:
        """Commit all vault changes; return the short sha or None if clean."""

        def _snap() -> str | None:
            self._git("add", "-A")
            status = self._git("status", "--porcelain")
            if not status.stdout.strip():
                return None
            commit = self._git("commit", "-m", message)
            if commit.returncode != 0:
                return None
            return self._git("rev-parse", "--short", "HEAD").stdout.strip() or None

        return await asyncio.get_running_loop().run_in_executor(None, _snap)

    async def log(self, limit: int = 10) -> list[str]:
        def _log() -> list[str]:
            result = self._git("log", "--oneline", "-n", str(limit))
            if result.returncode != 0:
                return []
            return [line for line in result.stdout.splitlines() if line.strip()]

        return await asyncio.get_running_loop().run_in_executor(None, _log)

    def _is_protected(self, path: str, protected: tuple[str, ...]) -> bool:
        return any(path == folder or path.startswith(folder + "/") for folder in protected)

    async def revert(self, rev: str, include_protected: bool = False) -> dict[str, Any]:
        """Restore vault files to `rev`, preserving protected folders by default."""

        def _revert() -> dict[str, Any]:
            changed = self._git("diff", "--name-status", "%s..HEAD" % rev)
            if changed.returncode != 0:
                return {"success": False, "message": "Revision no encontrada: %s" % rev}
            protected = get_taxonomy().protected_folders
            restored: list[str] = []
            removed: list[str] = []
            skipped = 0
            for line in changed.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                status, path = parts[0], parts[-1]
                if not include_protected and self._is_protected(path, protected):
                    skipped += 1
                    continue
                if status.startswith("A"):
                    removed.append(path)
                else:
                    restored.append(path)
            if not restored and not removed:
                return {
                    "success": False,
                    "message": "No hay cambios que revertir (protegidas omitidas: %d)" % skipped,
                    "protected_skipped": skipped,
                }
            if restored:
                self._git("checkout", rev, "--", *restored)
            if removed:
                self._git("rm", "-f", "--", *removed)
            commit = self._git("commit", "-m", "Revert vault to %s" % rev)
            if commit.returncode != 0:
                return {
                    "success": False,
                    "message": commit.stderr.strip()[:200],
                    "protected_skipped": skipped,
                }
            sha = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            logger.info(
                "BrainMaintainer: reverted to %s (restored=%d removed=%d protected_skipped=%d)",
                rev,
                len(restored),
                len(removed),
                skipped,
            )
            return {
                "success": True,
                "commit": sha,
                "restored": len(restored),
                "removed": len(removed),
                "protected_skipped": skipped,
            }

        return await asyncio.get_running_loop().run_in_executor(None, _revert)

    async def start(self, shutdown_event: asyncio.Event) -> None:
        if not settings.brain_maintenance:
            logger.info("BrainMaintainer disabled (BRAIN_MAINTENANCE=false)")
            return
        self._shutdown_event = shutdown_event
        await self.initialize()
        await self.snapshot("brain snapshot (startup)")
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "BrainMaintainer started (interval=%ds, protected=%s)",
            self.interval,
            get_taxonomy().protected_folders,
        )

    async def _loop(self) -> None:
        assert self._shutdown_event is not None
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.interval)
                return
            except TimeoutError:
                await self.snapshot("brain snapshot (periodic)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("BrainMaintainer stopped")
