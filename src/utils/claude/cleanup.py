"""
Claude Config Manager — cleanup operations (dead projects, dirs, history, plugins, binaries, stale files).
"""

import logging
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _CleanupMixin:
    """Cleanup and maintenance operations. Requires _ClaudeConfigBase attributes."""

    def preview_dead_projects(self) -> list[dict]:
        """
        Preview which project caches would be removed (without deleting).
        Scans all managed .claude directories.

        Returns:
            List of projects that would be removed with details
        """
        dead_projects = []

        for projects_dir in self.all_projects_dirs:
            try:
                for project_dir in projects_dir.iterdir():
                    if not project_dir.is_dir():
                        continue

                    cache_name = project_dir.name
                    original_path = cache_name.replace("-", "/")
                    path_exists = Path(original_path).exists()

                    if not path_exists:
                        size = self.get_directory_size(project_dir)

                        dead_projects.append(
                            {
                                "cache_name": cache_name,
                                "cache_path": str(project_dir),
                                "original_path": original_path,
                                "size_bytes": size,
                                "size_mb": round(size / (1024 * 1024), 2),
                                "reason": "Original project path does not exist",
                                "source": str(projects_dir.parent),
                            }
                        )
            except Exception as e:
                logger.error("Error previewing dead projects in %s: %s", projects_dir, e)

        return dead_projects

    def clean_dead_projects(self, confirmed_projects: list[str] | None = None) -> tuple[bool, str, dict]:
        """
        Remove project cache directories that no longer exist on disk

        Args:
            confirmed_projects: List of cache directory names to delete (for safety)

        Returns:
            Tuple of (success, message, details_dict)
        """
        if not self.projects_dir.exists():
            return True, "No projects directory found", {"removed": 0, "size_freed_mb": 0}

        removed_count = 0
        size_freed = 0
        removed_projects = []

        try:
            dead_projects = self.preview_dead_projects()

            if confirmed_projects:
                dead_projects = [p for p in dead_projects if p["cache_name"] in confirmed_projects]

            for project in dead_projects:
                project_dir = Path(project["cache_path"])
                shutil.rmtree(project_dir)
                removed_count += 1
                size_freed += project.get("size_bytes", 0)
                removed_projects.append({"name": project["cache_name"], "reason": project["reason"]})
                logger.info("Removed dead project cache: %s", project["cache_name"])

            size_mb = round(size_freed / (1024 * 1024), 2)

            self.invalidate_cache()

            return (
                True,
                f"Removed {removed_count} dead project(s), freed {size_mb} MB",
                {"removed": removed_count, "size_freed_mb": size_mb, "projects": removed_projects},
            )

        except Exception as e:
            logger.error("Error cleaning dead projects: %s", e)
            return False, str(e), {}

    def get_dir_stats(self, key: str) -> dict[str, Any]:
        """Get aggregated stats for a cleanable directory across all managed dirs.

        Args:
            key: Key from CLEANABLE_DIRS (e.g. 'debug', 'todos', 'cache')
        """
        dir_name, age_threshold = self.CLEANABLE_DIRS[key]

        combined = {"exists": False, "total_size_mb": 0, "file_count": 0, "old": {"count": 0, "size_mb": 0}}
        for claude_d in self.claude_dirs:
            stats = self._get_simple_dir_stats(claude_d / dir_name, age_threshold)
            if stats["exists"]:
                combined["exists"] = True
                combined["total_size_mb"] += stats["total_size_mb"]
                combined["file_count"] += stats["file_count"]
                combined["old"]["count"] += stats["old"]["count"]
                combined["old"]["size_mb"] += stats["old"]["size_mb"]

        combined["total_size_mb"] = round(combined["total_size_mb"], 2)
        combined["old"]["size_mb"] = round(combined["old"]["size_mb"], 2)
        return combined

    def clean_dir(self, key: str, max_age_days: int | None = None) -> tuple[bool, str, dict]:
        """Clean a cleanable directory across all managed dirs.

        Args:
            key: Key from CLEANABLE_DIRS (e.g. 'debug', 'todos', 'cache')
            max_age_days: Delete files older than this (None = delete all)
        """
        dir_name, _ = self.CLEANABLE_DIRS[key]
        total_deleted = 0
        total_freed = 0.0

        for claude_d in self.claude_dirs:
            dir_path = claude_d / dir_name
            if dir_path.exists():
                success, _msg, details = self._clean_simple_dir(dir_path, max_age_days)
                if success:
                    deleted = details.get("deleted", 0)
                    if isinstance(deleted, int):
                        total_deleted += deleted
                    total_freed += details.get("size_freed_mb", 0)

        return (
            True,
            f"Cleaned {dir_name} across {len(self.claude_dirs)} dir(s), freed {round(total_freed, 2)} MB",
            {"deleted": total_deleted, "size_freed_mb": round(total_freed, 2)},
        )

    def get_history_stats(self) -> dict[str, Any]:
        """Get aggregated history.jsonl statistics across all managed dirs."""
        total_size = 0
        total_lines = 0
        found = False

        for claude_d in self.claude_dirs:
            history_file = claude_d / "history.jsonl"
            if not history_file.exists():
                continue
            found = True
            try:
                total_size += history_file.stat().st_size
                with open(history_file, encoding="utf-8", errors="ignore") as f:
                    for _ in f:
                        total_lines += 1
            except (OSError, PermissionError) as e:
                logger.warning("Error reading history in %s: %s", claude_d, e)

        return {"exists": found, "size_mb": round(total_size / (1024 * 1024), 2), "line_count": total_lines}

    def clean_history(self, keep_last_n: int | None = None) -> tuple[bool, str, dict]:
        """
        Clean history.jsonl across all managed dirs.

        Args:
            keep_last_n: Number of recent entries to keep per dir (None = delete all)
        """
        total_freed = 0.0
        total_deleted = 0

        for claude_d in self.claude_dirs:
            history_file = claude_d / "history.jsonl"
            if not history_file.exists():
                continue

            try:
                original_size = history_file.stat().st_size

                if keep_last_n is None or keep_last_n == 0:
                    history_file.unlink()
                    total_freed += original_size / (1024 * 1024)
                    total_deleted += 1
                else:
                    with open(history_file, encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()

                    if len(lines) <= keep_last_n:
                        continue

                    kept_lines = lines[-keep_last_n:]
                    tmp_path = history_file.with_suffix(".tmp")
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        f.writelines(kept_lines)
                    os.replace(tmp_path, history_file)

                    new_size = history_file.stat().st_size
                    total_freed += (original_size - new_size) / (1024 * 1024)
                    total_deleted += len(lines) - keep_last_n

            except Exception as e:
                logger.error("Error cleaning history in %s: %s", claude_d, e)

        self.invalidate_cache()
        return (
            True,
            f"Cleaned history across {len(self.claude_dirs)} dir(s), freed {round(total_freed, 2)} MB",
            {"deleted": total_deleted, "size_freed_mb": round(total_freed, 2)},
        )

    def get_plugins_cache_stats(self) -> dict[str, Any]:
        """Get aggregated plugins cache statistics across all managed dirs."""
        total_size = 0
        file_count = 0
        found = False

        for claude_d in self.claude_dirs:
            cache_dir = claude_d / "plugins" / "cache"
            if not cache_dir.exists():
                continue
            found = True
            try:
                for file_path in cache_dir.rglob("*"):
                    if file_path.is_file():
                        file_count += 1
                        total_size += file_path.stat().st_size
            except (OSError, PermissionError) as e:
                logger.warning("Error reading plugins cache in %s: %s", claude_d, e)

        return {"exists": found, "total_size_mb": round(total_size / (1024 * 1024), 2), "file_count": file_count}

    def clean_plugins_cache(self) -> tuple[bool, str, dict]:
        """Clean plugins cache across all managed dirs."""
        total_freed = 0.0

        for claude_d in self.claude_dirs:
            cache_dir = claude_d / "plugins" / "cache"
            if not cache_dir.exists():
                continue
            try:
                size_freed = self.get_directory_size(cache_dir)
                shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                total_freed += size_freed / (1024 * 1024)
            except Exception as e:
                logger.error("Error cleaning plugins cache in %s: %s", claude_d, e)

        self.invalidate_cache()
        return (
            True,
            f"Cleaned plugins cache, freed {round(total_freed, 2)} MB",
            {"size_freed_mb": round(total_freed, 2)},
        )

    # --- Old Binaries Management ---

    def get_binaries_stats(self) -> dict[str, Any]:
        """
        Get stats for Claude binary versions at ~/.local/share/claude/versions/

        Returns:
            Dict with total_size_mb, version_count, versions list, latest version
        """
        versions_dir = Path.home() / ".local" / "share" / "claude" / "versions"

        if not versions_dir.exists():
            return {"exists": False, "total_size_mb": 0, "version_count": 0, "versions": [], "latest": None}

        versions: list[dict[str, Any]] = []
        total_size = 0

        try:
            for item in versions_dir.iterdir():
                if item.is_dir():
                    size = self.get_directory_size(item)
                    total_size += size
                    versions.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "size_bytes": size,
                            "size_mb": round(size / (1024 * 1024), 2),
                            "mtime": item.stat().st_mtime,
                        }
                    )
                elif item.is_file():
                    size = item.stat().st_size
                    total_size += size
                    versions.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "size_bytes": size,
                            "size_mb": round(size / (1024 * 1024), 2),
                            "mtime": item.stat().st_mtime,
                        }
                    )
        except (OSError, PermissionError) as e:
            logger.warning("Error reading versions directory: %s", e)

        # Sort by mtime descending (latest first)
        versions.sort(key=lambda x: x["mtime"], reverse=True)

        latest = versions[0] if versions else None

        return {
            "exists": True,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "version_count": len(versions),
            "versions": versions,
            "latest": latest,
        }

    def clean_old_binaries(self) -> tuple[bool, str, dict]:
        """
        Delete all binary versions except the latest one.

        Returns:
            Tuple of (success, message, details)
        """
        stats = self.get_binaries_stats()

        if not stats["exists"] or stats["version_count"] <= 1:
            return True, "Nothing to clean (0 or 1 version)", {"deleted": 0, "size_freed_mb": 0}

        latest = stats["latest"]
        deleted_count = 0
        size_freed = 0

        try:
            for version in stats["versions"]:
                if version["path"] == latest["path"]:
                    continue  # Skip the latest

                path = Path(version["path"])
                size_freed += version["size_bytes"]

                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except FileNotFoundError:
                    pass

                deleted_count += 1
                logger.info("Deleted old binary version: %s", version["name"])

            self.invalidate_cache()
            size_mb = round(size_freed / (1024 * 1024), 2)

            return (
                True,
                f"Deleted {deleted_count} old version(s), kept {latest['name']}, freed {size_mb} MB",
                {"deleted": deleted_count, "kept": latest["name"], "size_freed_mb": size_mb},
            )
        except Exception as e:
            logger.error("Error cleaning old binaries: %s", e)
            return False, str(e), {}

    # --- Stale Root Files ---

    def get_stale_files_stats(self) -> dict[str, Any]:
        """
        Find stale root files across all managed .claude dirs.

        Returns:
            Dict with file list and total size
        """
        stale_files = []
        total_size = 0

        for claude_d in self.claude_dirs:
            try:
                for f in claude_d.glob("security_warnings_state_*.json"):
                    if f.is_file():
                        size = f.stat().st_size
                        total_size += size
                        stale_files.append(
                            {"name": f.name, "path": str(f), "size_bytes": size, "size_mb": round(size / (1024 * 1024), 4)}
                        )

                stats_cache = claude_d / "stats-cache.json"
                if stats_cache.is_file():
                    size = stats_cache.stat().st_size
                    total_size += size
                    stale_files.append(
                        {
                            "name": stats_cache.name,
                            "path": str(stats_cache),
                            "size_bytes": size,
                            "size_mb": round(size / (1024 * 1024), 4),
                        }
                    )
            except (OSError, PermissionError) as e:
                logger.warning("Error scanning stale files in %s: %s", claude_d, e)

        return {
            "exists": len(stale_files) > 0,
            "file_count": len(stale_files),
            "total_size_mb": round(total_size / (1024 * 1024), 4),
            "files": stale_files,
        }

    def clean_stale_files(self) -> tuple[bool, str, dict]:
        """Delete stale root files from all managed .claude dirs."""
        stats = self.get_stale_files_stats()

        if not stats["exists"]:
            return True, "No stale files found", {"deleted": 0, "size_freed_mb": 0}

        deleted_count = 0
        size_freed = 0

        try:
            for file_info in stats["files"]:
                path = Path(file_info["path"])
                try:
                    path.unlink()
                    size_freed += file_info["size_bytes"]
                    deleted_count += 1
                    logger.info("Deleted stale file: %s", file_info["name"])
                except FileNotFoundError:
                    continue

            size_mb = round(size_freed / (1024 * 1024), 4)
            return (
                True,
                f"Deleted {deleted_count} stale file(s), freed {size_mb} MB",
                {"deleted": deleted_count, "size_freed_mb": size_mb},
            )
        except Exception as e:
            logger.error("Error cleaning stale files: %s", e)
            return False, str(e), {}

    def clean_all(self, keep_projects: bool = True) -> tuple[bool, str, dict]:
        """
        Clean all cleanable data at once

        Args:
            keep_projects: If True, don't delete project caches (default: True)
        """
        total_freed = 0
        results = {}

        # Clean each category — cleanable dirs use generic clean_dir()
        cleaners: list[tuple[str, Callable[[], tuple[bool, str, dict]]]] = [
            (key, lambda k=key: self.clean_dir(k))
            for key in self.CLEANABLE_DIRS
        ]
        # Add non-dir cleaners with unique logic
        cleaners.extend([
            ("history", lambda: self.clean_history(None)),
            ("plugins_cache", lambda: self.clean_plugins_cache()),
            ("stale_files", lambda: self.clean_stale_files()),
            ("old_binaries", lambda: self.clean_old_binaries()),
        ])

        for name, cleaner in cleaners:
            try:
                success, _message, details = cleaner()
                freed = details.get("size_freed_mb", 0)
                total_freed += freed
                results[name] = {"success": success, "freed_mb": freed}
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}

        self.invalidate_cache()

        return (
            True,
            f"Cleaned all, freed {round(total_freed, 2)} MB total",
            {"total_freed_mb": round(total_freed, 2), "details": results},
        )
