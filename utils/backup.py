"""
Guild configuration backup and restore utilities.

Saves a timestamped JSON snapshot of a guild's config to disk and
allows restoration from any previous snapshot.

Backup directory structure:
    data/backups/
        {guild_id}/
            {iso_timestamp}.json
            {iso_timestamp}.json
            ...

Usage:
    from utils.backup import BackupManager
    bm = BackupManager()

    await bm.create(guild_id, config_dict)
    snapshots = bm.list_snapshots(guild_id)
    data = bm.load(guild_id, snapshots[0])
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("bot.backup")

_BACKUP_ROOT  = Path("data/backups")
_MAX_BACKUPS  = 10          # per guild — older ones are pruned automatically
_ENCODING     = "utf-8"


class BackupManager:
    """Manages per-guild configuration snapshots on disk."""

    def __init__(self, root: Path = _BACKUP_ROOT) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    # ── Core ───────────────────────────────────────────────────

    def create(
        self,
        guild_id: int,
        data:     dict[str, Any],
    ) -> Path:
        """
        Write *data* to a timestamped JSON file for *guild_id*.

        Automatically prunes old snapshots beyond ``_MAX_BACKUPS``.

        Returns the :class:`~pathlib.Path` of the written file.
        """
        guild_dir = self._guild_dir(guild_id)
        ts        = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path      = guild_dir / f"{ts}.json"

        try:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding=_ENCODING,
            )
            log.debug("Backup created: %s", path)
        except OSError as exc:
            log.error("Failed to write backup %s: %s", path, exc)
            raise

        self._prune(guild_id)
        return path

    def list_snapshots(self, guild_id: int) -> list[str]:
        """
        Return a list of snapshot filenames for *guild_id*, newest first.

        Each name is just the filename stem (timestamp string).
        """
        guild_dir = self._guild_dir(guild_id)
        files     = sorted(
            guild_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [f.stem for f in files]

    def load(self, guild_id: int, snapshot: str) -> dict[str, Any]:
        """
        Load a specific snapshot by its stem name.

        Raises ``FileNotFoundError`` if the snapshot does not exist.
        Raises ``ValueError`` if the file cannot be decoded as JSON.
        """
        path = self._guild_dir(guild_id) / f"{snapshot}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Snapshot '{snapshot}' not found for guild {guild_id}."
            )
        try:
            return json.loads(path.read_text(encoding=_ENCODING))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Snapshot '{snapshot}' is not valid JSON: {exc}") from exc

    def delete(self, guild_id: int, snapshot: str) -> bool:
        """
        Delete a specific snapshot.

        Returns True if deleted, False if it did not exist.
        """
        path = self._guild_dir(guild_id) / f"{snapshot}.json"
        if path.exists():
            path.unlink()
            log.debug("Backup deleted: %s", path)
            return True
        return False

    def delete_all(self, guild_id: int) -> int:
        """
        Delete all snapshots for *guild_id*.

        Returns the number of files removed.
        """
        guild_dir = self._guild_dir(guild_id)
        removed   = 0
        for f in guild_dir.glob("*.json"):
            f.unlink()
            removed += 1
        log.info("Deleted %d backup(s) for guild %d", removed, guild_id)
        return removed

    def snapshot_count(self, guild_id: int) -> int:
        """Return how many snapshots exist for *guild_id*."""
        return len(list(self._guild_dir(guild_id).glob("*.json")))

    # ── Internals ──────────────────────────────────────────────

    def _guild_dir(self, guild_id: int) -> Path:
        path = self._root / str(guild_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _prune(self, guild_id: int) -> None:
        """Remove oldest snapshots when count exceeds ``_MAX_BACKUPS``."""
        guild_dir = self._guild_dir(guild_id)
        files     = sorted(
            guild_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(files) > _MAX_BACKUPS:
            oldest = files.pop(0)
            oldest.unlink()
            log.debug("Pruned old backup: %s", oldest)