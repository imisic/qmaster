"""
Claude Config Manager — advanced cleanup: subagent logs, orphaned project
caches, and miscellaneous small caches not covered by the generic cleaner.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Misc small-cache paths under ~/.claude/ that CC regenerates on demand.
# Safe to delete outright. Anything missing from this list is intentional.
MISC_CLAUDE_PATHS = (
    "usage-data",
    "backups",
    "sessions",
    "teams",
    "reports",
)

MISC_CLAUDE_FILES = (
    "mcp.json.backup",
)

# Glob patterns for one-off root files. Keep this disjoint from the
# stale-files list in cleanup.py (security_warnings_state_*.json, stats-cache.json)
# so a single category isn't tallied twice across mixins.
MISC_CLAUDE_GLOBS: tuple[str, ...] = ()


class _AdvancedCleanupMixin:
    """Subagent logs, orphaned projects, and misc cache cleaning."""

    # ─────────────────────────────────────────────────────────────────
    # Subagent logs
    # ─────────────────────────────────────────────────────────────────

    def get_subagent_stats(self, max_age_days: int = 30) -> dict[str, Any]:
        """
        Walk projects/*/<session>/subagents/ and tally size + age.

        Returns totals plus an 'old' bucket for sessions whose parent JSONL
        (or subagents dir) is older than max_age_days.
        """
        if not self.projects_dir.exists():
            return {"exists": False, "total_size_mb": 0, "file_count": 0, "session_count": 0, "old": {}}

        cutoff = datetime.now().timestamp() - (max_age_days * 86400)
        total_size = 0
        file_count = 0
        session_count = 0
        old_size = 0
        old_files = 0
        old_sessions = 0

        for project in self._iter_projects():
            for _session_dir, sa_dir, last_mtime in self._iter_session_subagents(project):
                session_count += 1
                session_bytes = 0
                session_files = 0
                for f in sa_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    try:
                        st = f.stat()
                    except OSError as e:
                        logger.debug("stat failed %s: %s", f, e)
                        continue
                    session_bytes += st.st_size
                    session_files += 1
                total_size += session_bytes
                file_count += session_files
                if last_mtime < cutoff:
                    old_size += session_bytes
                    old_files += session_files
                    old_sessions += 1

        return {
            "exists": file_count > 0,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
            "session_count": session_count,
            "old": {
                "size_mb": round(old_size / (1024 * 1024), 2),
                "file_count": old_files,
                "session_count": old_sessions,
                "threshold_days": max_age_days,
            },
        }

    def clean_subagent_logs(self, max_age_days: int | None = 30) -> tuple[bool, str, dict]:
        """
        Delete `subagents/` directories for old sessions.

        Args:
            max_age_days: Delete subagents for sessions whose parent JSONL is
                          older than this. None = delete ALL subagents.
        """
        if not self.projects_dir.exists():
            return True, "No projects directory", {"deleted": 0, "size_freed_mb": 0}

        cutoff = None
        if max_age_days is not None:
            cutoff = datetime.now().timestamp() - (max_age_days * 86400)

        deleted_sessions = 0
        size_freed = 0

        for project in self._iter_projects():
            for _session_dir, sa_dir, last_mtime in self._iter_session_subagents(project):
                if cutoff is not None and last_mtime >= cutoff:
                    continue
                try:
                    sa_size = sum(f.stat().st_size for f in sa_dir.rglob("*") if f.is_file())
                except OSError as e:
                    logger.warning("Cannot size %s: %s", sa_dir, e)
                    continue
                try:
                    shutil.rmtree(sa_dir)
                except OSError as e:
                    logger.warning("Cannot delete %s: %s", sa_dir, e)
                    continue
                size_freed += sa_size
                deleted_sessions += 1
                logger.info("Deleted subagent logs: %s", sa_dir)

        self.invalidate_cache()
        size_mb = round(size_freed / (1024 * 1024), 2)
        if deleted_sessions == 0:
            return True, "No old subagent logs to delete", {"deleted": 0, "size_freed_mb": 0}
        return (
            True,
            f"Deleted subagent logs for {deleted_sessions} session(s), freed {size_mb} MB",
            {"deleted": deleted_sessions, "size_freed_mb": size_mb},
        )

    def _iter_projects(self):
        """Yield project directories under ~/.claude/projects/."""
        try:
            for p in self.projects_dir.iterdir():
                if p.is_dir():
                    yield p
        except (OSError, PermissionError) as e:
            logger.warning("Cannot list projects dir: %s", e)

    def _iter_session_subagents(self, project: Path):
        """
        Yield (session_dir, subagents_dir, effective_mtime) for each session
        in a project that has a subagents/ directory.

        effective_mtime prefers the parent session JSONL's mtime (the "real"
        last use), falling back to the subagents dir mtime.
        """
        try:
            children = list(project.iterdir())
        except (OSError, PermissionError):
            return
        for child in children:
            if not child.is_dir() or child.name == "memory":
                continue
            sa_dir = child / "subagents"
            if not sa_dir.is_dir():
                continue
            mtime = 0.0
            parent_jsonl = project / f"{child.name}.jsonl"
            try:
                if parent_jsonl.exists():
                    mtime = parent_jsonl.stat().st_mtime
                else:
                    mtime = sa_dir.stat().st_mtime
            except OSError as e:
                logger.debug("mtime probe failed %s: %s", parent_jsonl, e)
            yield child, sa_dir, mtime

    # ─────────────────────────────────────────────────────────────────
    # Orphaned project caches
    # ─────────────────────────────────────────────────────────────────

    def get_orphan_projects_stats(self) -> dict[str, Any]:
        """
        Find project caches whose real working directory no longer exists.

        A project is orphaned when neither cwd-peek nor filesystem probe
        finds a matching directory on disk.
        """
        if not self.projects_dir.exists():
            return {"exists": False, "count": 0, "total_size_mb": 0, "projects": []}

        orphans: list[dict[str, Any]] = []
        total_size = 0
        for project in self._iter_projects():
            if self._resolved_or_none(project.name) is not None:
                continue
            size = self.get_directory_size(project)
            total_size += size
            orphans.append(
                {
                    "name": project.name,
                    "guessed_path": "/" + project.name.lstrip("-").replace("-", "/"),
                    "size_mb": round(size / (1024 * 1024), 2),
                    "path": str(project),
                }
            )

        return {
            "exists": len(orphans) > 0,
            "count": len(orphans),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "projects": orphans,
        }

    def clean_orphan_projects(self) -> tuple[bool, str, dict]:
        """Delete every project cache whose real working directory is gone."""
        stats = self.get_orphan_projects_stats()
        if not stats["exists"]:
            return True, "No orphaned project caches found", {"deleted": 0, "size_freed_mb": 0}

        deleted = 0
        size_freed = 0.0
        for orphan in stats["projects"]:
            path = Path(orphan["path"])
            try:
                shutil.rmtree(path)
            except OSError as e:
                logger.warning("Cannot delete orphan %s: %s", path, e)
                continue
            deleted += 1
            size_freed += orphan["size_mb"]
            logger.info("Deleted orphan project cache: %s", path)

        self.invalidate_cache()
        return (
            True,
            f"Deleted {deleted} orphaned project cache(s), freed {size_freed:.2f} MB",
            {"deleted": deleted, "size_freed_mb": round(size_freed, 2)},
        )

    def _resolved_or_none(self, encoded: str) -> str | None:
        """Return the real path if it exists on disk, else None."""
        decoded = self._decode_project_name(encoded)
        return decoded if Path(decoded).is_dir() else None

    # ─────────────────────────────────────────────────────────────────
    # Misc small caches
    # ─────────────────────────────────────────────────────────────────

    def get_misc_claude_stats(self) -> dict[str, Any]:
        """Total size of misc-cache paths and files under ~/.claude/."""
        total_size = 0
        item_count = 0
        parts: list[tuple[str, int]] = []

        for name in MISC_CLAUDE_PATHS:
            path = self.claude_dir / name
            try:
                size = self._path_size(path)
            except FileNotFoundError:
                continue
            if size == 0 and not path.exists():
                continue
            total_size += size
            item_count += 1
            parts.append((name, size))

        for name in MISC_CLAUDE_FILES:
            path = self.claude_dir / name
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.debug("stat failed %s: %s", path, e)
                continue
            total_size += size
            item_count += 1
            parts.append((name, size))

        for pattern in MISC_CLAUDE_GLOBS:
            for path in self.claude_dir.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError as e:
                    logger.debug("stat failed %s: %s", path, e)
                    continue
                total_size += size
                item_count += 1
                parts.append((path.name, size))

        return {
            "exists": item_count > 0,
            "total_size_mb": round(total_size / (1024 * 1024), 4),
            "item_count": item_count,
            "items": [{"name": n, "size_mb": round(s / (1024 * 1024), 4)} for n, s in parts],
        }

    def clean_misc_claude(self) -> tuple[bool, str, dict]:
        """Delete every misc-cache path and file. CC regenerates what it needs."""
        stats = self.get_misc_claude_stats()
        if not stats["exists"]:
            return True, "No misc caches to delete", {"deleted": 0, "size_freed_mb": 0}

        deleted = 0
        size_freed = 0

        for name in MISC_CLAUDE_PATHS:
            path = self.claude_dir / name
            try:
                size = self._path_size(path)
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted += 1
                size_freed += size
                logger.info("Deleted misc cache: %s", path)
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning("Cannot delete %s: %s", path, e)

        for name in MISC_CLAUDE_FILES:
            path = self.claude_dir / name
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                path.unlink()
                deleted += 1
                size_freed += size
                logger.info("Deleted misc file: %s", path)
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning("Cannot delete %s: %s", path, e)

        for pattern in MISC_CLAUDE_GLOBS:
            for path in self.claude_dir.glob(pattern):
                try:
                    if not path.is_file():
                        continue
                    size = path.stat().st_size
                    path.unlink()
                    deleted += 1
                    size_freed += size
                    logger.info("Deleted misc match: %s", path)
                except FileNotFoundError:
                    continue
                except OSError as e:
                    logger.warning("Cannot delete %s: %s", path, e)

        self.invalidate_cache()
        size_mb = round(size_freed / (1024 * 1024), 4)
        return (
            True,
            f"Deleted {deleted} misc item(s), freed {size_mb} MB",
            {"deleted": deleted, "size_freed_mb": size_mb},
        )

    def _path_size(self, path: Path) -> int:
        """Bytes for a file or directory tree."""
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        return self.get_directory_size(path)
