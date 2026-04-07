"""
Claude Config Manager — cleanup operations (dead projects, dirs, history, plugins, binaries, stale files).
"""

import logging
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
        Preview which project caches would be removed (without deleting)

        Returns:
            List of projects that would be removed with details
        """
        if not self.projects_dir.exists():
            return []

        dead_projects = []

        try:
            for project_dir in self.projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue

                # Reconstruct original project path from cache directory name
                # Cache name: "-home-user-projects-myapp" -> "/home/user/projects/myapp"
                cache_name = project_dir.name

                # Replace dashes with slashes to get original path
                original_path = cache_name.replace("-", "/")

                # Check if it's an old Windows mount
                is_old_mount = "/mnt/c/" in original_path or "/mnt/d/" in original_path

                # Check if original path exists on disk
                path_exists = Path(original_path).exists()

                if is_old_mount or not path_exists:
                    size = self.get_directory_size(project_dir)

                    reason = "Old Windows mount path" if is_old_mount else "Original project path does not exist"

                    dead_projects.append(
                        {
                            "cache_name": cache_name,
                            "cache_path": str(project_dir),
                            "original_path": original_path,
                            "size_mb": round(size / (1024 * 1024), 2),
                            "reason": reason,
                        }
                    )

        except Exception as e:
            logger.error(f"Error previewing dead projects: {e}")

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

            # If confirmed_projects is provided, only delete those
            if confirmed_projects:
                dead_projects = [p for p in dead_projects if p["cache_name"] in confirmed_projects]

            for project in dead_projects:
                project_dir = Path(project["cache_path"])
                size = self.get_directory_size(project_dir)
                shutil.rmtree(project_dir)
                removed_count += 1
                size_freed += size
                removed_projects.append({"name": project["cache_name"], "reason": project["reason"]})
                logger.info(f"Removed dead project cache: {project['cache_name']}")

            size_mb = round(size_freed / (1024 * 1024), 2)

            # Invalidate cache after cleanup
            self.invalidate_cache()

            return (
                True,
                f"Removed {removed_count} dead project(s), freed {size_mb} MB",
                {"removed": removed_count, "size_freed_mb": size_mb, "projects": removed_projects},
            )

        except Exception as e:
            logger.error(f"Error cleaning dead projects: {e}")
            return False, str(e), {}

    def get_dir_stats(self, key: str) -> dict[str, Any]:
        """Get stats for a cleanable directory by key.

        Args:
            key: Key from CLEANABLE_DIRS (e.g. 'debug', 'todos', 'cache')
        """
        dir_name, age_threshold = self.CLEANABLE_DIRS[key]
        return self._get_simple_dir_stats(self.claude_dir / dir_name, age_threshold)

    def clean_dir(self, key: str, max_age_days: int | None = None) -> tuple[bool, str, dict]:
        """Clean a cleanable directory by key.

        Args:
            key: Key from CLEANABLE_DIRS (e.g. 'debug', 'todos', 'cache')
            max_age_days: Delete files older than this (None = delete all)
        """
        dir_name, _ = self.CLEANABLE_DIRS[key]
        return self._clean_simple_dir(self.claude_dir / dir_name, max_age_days)

    def get_history_stats(self) -> dict[str, Any]:
        """Get history.jsonl statistics"""
        history_file = self.claude_dir / "history.jsonl"

        if not history_file.exists():
            return {"exists": False, "size_mb": 0, "line_count": 0}

        try:
            size = history_file.stat().st_size
            # Count lines
            line_count = 0
            with open(history_file, encoding="utf-8", errors="ignore") as f:
                for _ in f:
                    line_count += 1

            return {"exists": True, "size_mb": round(size / (1024 * 1024), 2), "line_count": line_count}
        except (OSError, PermissionError) as e:
            logger.warning(f"Error reading history: {e}")
            return {"exists": False, "size_mb": 0, "line_count": 0}

    def clean_history(self, keep_last_n: int | None = None) -> tuple[bool, str, dict]:
        """
        Clean history.jsonl - keep last N entries or delete all

        Args:
            keep_last_n: Number of recent entries to keep (None = delete all)
        """
        history_file = self.claude_dir / "history.jsonl"

        if not history_file.exists():
            return True, "No history file found", {"deleted": 0, "size_freed_mb": 0}

        try:
            original_size = history_file.stat().st_size

            if keep_last_n is None or keep_last_n == 0:
                # Delete entire file
                history_file.unlink()
                self.invalidate_cache()
                return (
                    True,
                    f"Deleted history, freed {round(original_size / (1024 * 1024), 2)} MB",
                    {"deleted": "all", "size_freed_mb": round(original_size / (1024 * 1024), 2)},
                )
            else:
                # Keep last N lines
                with open(history_file, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                original_count = len(lines)
                if original_count <= keep_last_n:
                    return True, f"Only {original_count} entries, nothing to delete", {"deleted": 0, "size_freed_mb": 0}

                # Keep last N lines
                kept_lines = lines[-keep_last_n:]
                with open(history_file, "w", encoding="utf-8") as f:
                    f.writelines(kept_lines)

                new_size = history_file.stat().st_size
                size_freed = original_size - new_size
                deleted_count = original_count - keep_last_n

                self.invalidate_cache()
                return (
                    True,
                    f"Kept {keep_last_n} entries, deleted {deleted_count}",
                    {"deleted": deleted_count, "size_freed_mb": round(size_freed / (1024 * 1024), 2)},
                )

        except Exception as e:
            logger.error(f"Error cleaning history: {e}")
            return False, str(e), {}

    def get_plugins_cache_stats(self) -> dict[str, Any]:
        """Get plugins cache statistics"""
        cache_dir = self.claude_dir / "plugins" / "cache"

        if not cache_dir.exists():
            return {"exists": False, "total_size_mb": 0, "file_count": 0}

        total_size = 0
        file_count = 0

        try:
            for file_path in cache_dir.rglob("*"):
                if file_path.is_file():
                    file_count += 1
                    total_size += file_path.stat().st_size
        except (OSError, PermissionError) as e:
            logger.warning(f"Error reading plugins cache: {e}")

        return {"exists": True, "total_size_mb": round(total_size / (1024 * 1024), 2), "file_count": file_count}

    def clean_plugins_cache(self) -> tuple[bool, str, dict]:
        """Clean plugins cache - safe to delete, will re-download on next use"""
        cache_dir = self.claude_dir / "plugins" / "cache"

        if not cache_dir.exists():
            return True, "No plugins cache found", {"deleted": 0, "size_freed_mb": 0}

        try:
            size_freed = self.get_directory_size(cache_dir)
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.invalidate_cache()

            return (
                True,
                f"Deleted plugins cache, freed {round(size_freed / (1024 * 1024), 2)} MB",
                {"deleted": "all", "size_freed_mb": round(size_freed / (1024 * 1024), 2)},
            )
        except Exception as e:
            logger.error(f"Error cleaning plugins cache: {e}")
            return False, str(e), {}

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
            logger.warning(f"Error reading versions directory: {e}")

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
                logger.info(f"Deleted old binary version: {version['name']}")

            self.invalidate_cache()
            size_mb = round(size_freed / (1024 * 1024), 2)

            return (
                True,
                f"Deleted {deleted_count} old version(s), kept {latest['name']}, freed {size_mb} MB",
                {"deleted": deleted_count, "kept": latest["name"], "size_freed_mb": size_mb},
            )
        except Exception as e:
            logger.error(f"Error cleaning old binaries: {e}")
            return False, str(e), {}

    # --- Stale Root Files ---

    def get_stale_files_stats(self) -> dict[str, Any]:
        """
        Find stale root files in ~/.claude/ (security_warnings_state_*.json, stats-cache.json)

        Returns:
            Dict with file list and total size
        """
        stale_files = []
        total_size = 0

        try:
            # security_warnings_state_*.json
            for f in self.claude_dir.glob("security_warnings_state_*.json"):
                if f.is_file():
                    size = f.stat().st_size
                    total_size += size
                    stale_files.append(
                        {"name": f.name, "path": str(f), "size_bytes": size, "size_mb": round(size / (1024 * 1024), 4)}
                    )

            # stats-cache.json
            stats_cache = self.claude_dir / "stats-cache.json"
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
            logger.warning(f"Error scanning stale files: {e}")

        return {
            "exists": len(stale_files) > 0,
            "file_count": len(stale_files),
            "total_size_mb": round(total_size / (1024 * 1024), 4),
            "files": stale_files,
        }

    def clean_stale_files(self) -> tuple[bool, str, dict]:
        """Delete stale root files from ~/.claude/"""
        stats = self.get_stale_files_stats()

        if not stats["exists"]:
            return True, "No stale files found", {"deleted": 0, "size_freed_mb": 0}

        deleted_count = 0
        size_freed = 0

        try:
            for file_info in stats["files"]:
                path = Path(file_info["path"])
                if path.exists():
                    size_freed += file_info["size_bytes"]
                    path.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted stale file: {file_info['name']}")

            size_mb = round(size_freed / (1024 * 1024), 4)
            return (
                True,
                f"Deleted {deleted_count} stale file(s), freed {size_mb} MB",
                {"deleted": deleted_count, "size_freed_mb": size_mb},
            )
        except Exception as e:
            logger.error(f"Error cleaning stale files: {e}")
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

        # Invalidate cache
        self.invalidate_cache()

        return (
            True,
            f"Cleaned all, freed {round(total_freed, 2)} MB total",
            {"total_freed_mb": round(total_freed, 2), "details": results},
        )
