"""
Claude Config Manager — base class with shared state, caching, and directory helpers.
"""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _ClaudeConfigBase:
    """Shared state and low-level helpers for Claude config management."""

    # Directories inside project caches that must never be deleted by cleanup
    PROTECTED_DIRS = frozenset({"memory"})

    # UUID pattern for session directories
    UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    DEFAULT_CACHE_TTL = 300  # 5 minutes

    # Health status thresholds (MB)
    HEALTH_GOOD_MB = 100
    HEALTH_WARNING_MB = 300

    # Data-driven cleanable directories: key -> (subdir_name, age_threshold_days)
    CLEANABLE_DIRS: dict[str, tuple[str, int]] = {
        "debug": ("debug", 30),
        "local_cache": ("local", 30),
        "file_history": ("file-history", 30),
        "todos": ("todos", 90),
        "shell_snapshots": ("shell-snapshots", 30),
        "session_env": ("session-env", 30),
        "plans": ("plans", 30),
        "image_cache": ("image-cache", 30),
        "paste_cache": ("paste-cache", 30),
        "cache": ("cache", 30),
        "tasks": ("tasks", 30),
        "statsig": ("statsig", 30),
        "ide": ("ide", 30),
        "telemetry": ("telemetry", 30),
    }

    def __init__(self, export_base_path: Path | None = None, cache_ttl: int = DEFAULT_CACHE_TTL):
        """
        Initialize Claude Config Manager

        Args:
            export_base_path: Base path for exports (default: ~/backups/claude_exports)
            cache_ttl: Cache time-to-live in seconds (default: 300 = 5 minutes)
        """
        self.claude_dir = Path.home() / ".claude"
        self.projects_dir = self.claude_dir / "projects"
        self.mcp_config_path = self.claude_dir / "mcp.json"
        self.export_base_path = export_base_path or Path.home() / "backups" / "claude_exports"
        self.export_base_path.mkdir(parents=True, exist_ok=True)

        # Cache for directory sizes (path -> (size, timestamp))
        self._size_cache: dict[str, tuple[int, float]] = {}
        self._cache_ttl = cache_ttl

    def invalidate_cache(self, directory: Path | None = None) -> None:
        """
        Invalidate size cache for a specific directory or all directories

        Args:
            directory: Specific directory to invalidate, or None for all
        """
        if directory:
            cache_key = str(directory)
            if cache_key in self._size_cache:
                del self._size_cache[cache_key]
                logger.debug(f"Invalidated cache for {directory}")
        else:
            self._size_cache.clear()
            logger.debug("Cleared entire size cache")

    def get_directory_size(self, directory: Path, use_cache: bool = True) -> int:
        """
        Calculate total size of a directory recursively (with caching)

        Args:
            directory: Path to directory
            use_cache: Whether to use cached values (default: True)

        Returns:
            Size in bytes
        """
        import time

        cache_key = str(directory)

        # Check cache if enabled
        if use_cache and cache_key in self._size_cache:
            cached_size, cached_time = self._size_cache[cache_key]
            age = time.time() - cached_time

            if age < self._cache_ttl:
                logger.debug(f"Using cached size for {directory} (age: {age:.1f}s)")
                return cached_size
            else:
                logger.debug(f"Cache expired for {directory} (age: {age:.1f}s)")

        # Calculate size
        total = 0
        try:
            for item in directory.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError) as e:
            logger.warning(f"Permission error accessing {directory}: {e}")

        # Update cache
        if use_cache:
            self._size_cache[cache_key] = (total, time.time())
            logger.debug(f"Cached size for {directory}: {total / (1024 * 1024):.2f} MB")

        return total

    # --- Generic Directory Helpers ---

    def _get_simple_dir_stats(self, dir_path: Path, age_threshold_days: int = 30) -> dict[str, Any]:
        """
        Generic stats for a simple directory.

        Returns:
            Dict with exists, total_size_mb, file_count, old files count/size
        """
        if not dir_path.exists():
            return {"exists": False, "total_size_mb": 0, "file_count": 0, "old": {"count": 0, "size_mb": 0}}

        total_size = 0
        file_count = 0
        old_count = 0
        old_size = 0

        now = datetime.now().timestamp()
        cutoff = now - (age_threshold_days * 24 * 3600)

        try:
            for file_path in dir_path.rglob("*"):
                if file_path.is_file():
                    file_count += 1
                    size = file_path.stat().st_size
                    total_size += size
                    if file_path.stat().st_mtime < cutoff:
                        old_count += 1
                        old_size += size
        except (OSError, PermissionError) as e:
            logger.warning(f"Error reading {dir_path}: {e}")

        return {
            "exists": True,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
            "old": {"count": old_count, "size_mb": round(old_size / (1024 * 1024), 2)},
        }

    def _clean_simple_dir(self, dir_path: Path, max_age_days: int | None = None) -> tuple[bool, str, dict]:
        """
        Generic cleanup for a simple directory.

        Args:
            dir_path: Directory to clean
            max_age_days: Delete files older than this (None = delete all)
        """
        if not dir_path.exists():
            return True, f"Not found: {dir_path.name}", {"deleted": 0, "size_freed_mb": 0}

        deleted_count = 0
        size_freed = 0

        try:
            if max_age_days is None:
                size_freed = self.get_directory_size(dir_path)
                shutil.rmtree(dir_path)
                dir_path.mkdir(parents=True, exist_ok=True)
                self.invalidate_cache()
                return (
                    True,
                    f"Deleted all from {dir_path.name}, freed {round(size_freed / (1024 * 1024), 2)} MB",
                    {"deleted": "all", "size_freed_mb": round(size_freed / (1024 * 1024), 2)},
                )
            else:
                cutoff_time = datetime.now().timestamp() - (max_age_days * 24 * 3600)
                for file_path in dir_path.rglob("*"):
                    if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                        size_freed += file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1

                self.invalidate_cache()
                size_mb = round(size_freed / (1024 * 1024), 2)
                return (
                    True,
                    f"Deleted {deleted_count} files from {dir_path.name}, freed {size_mb} MB",
                    {"deleted": deleted_count, "size_freed_mb": size_mb},
                )
        except Exception as e:
            logger.error(f"Error cleaning {dir_path}: {e}")
            return False, str(e), {}
