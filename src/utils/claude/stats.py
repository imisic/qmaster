"""
Claude Config Manager — statistics, project listing, and export/delete operations.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _StatsMixin:
    """Stats, project listing, and project export/delete. Requires _ClaudeConfigBase attributes."""

    def get_stats(self, use_cache: bool = False) -> dict[str, Any]:
        """
        Get overall Claude configuration statistics across all managed directories.

        Args:
            use_cache: Whether to use cached values (default: False for fresh data)

        Returns:
            Dictionary with size, count, and health metrics
        """
        if not any(d.exists() for d in self.claude_dirs):
            return {
                "exists": False,
                "total_size_bytes": 0,
                "total_size_mb": 0.0,
                "projects_count": 0,
                "projects_size_mb": 0.0,
                "health": "unknown",
                "largest_project": None,
                "dir_count": 0,
            }

        # Aggregate across all claude dirs
        total_size = sum(
            self.get_directory_size(d, use_cache=use_cache)
            for d in self.claude_dirs if d.exists()
        )

        projects_size = 0
        projects_count = 0
        for pd in self.all_projects_dirs:
            if pd.exists():
                projects_size += self.get_directory_size(pd, use_cache=use_cache)
                projects_count += len(list(pd.iterdir()))

        # Find largest project
        projects = self.list_projects()
        largest_project = max(projects, key=lambda x: x["size_bytes"]) if projects else None

        # Determine health status
        total_mb = total_size / (1024 * 1024)
        if total_mb < self.HEALTH_GOOD_MB:
            health = "good"
        elif total_mb < self.HEALTH_WARNING_MB:
            health = "warning"
        else:
            health = "critical"

        return {
            "exists": True,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_mb, 1),
            "projects_count": projects_count,
            "projects_size_mb": round(projects_size / (1024 * 1024), 1),
            "health": health,
            "largest_project": largest_project,
            "dir_count": len(self.claude_dirs),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        """
        List all Claude projects with their sizes and metadata across all managed directories.

        Returns:
            List of project dictionaries with path, size, date info
        """
        projects: list[dict[str, Any]] = []

        for projects_dir in self.all_projects_dirs:
            source_label = str(projects_dir.parent)
            try:
                for project_path in projects_dir.iterdir():
                    if not project_path.is_dir():
                        continue

                    try:
                        size_bytes = self.get_directory_size(project_path)
                        mtime = project_path.stat().st_mtime
                        last_modified = datetime.fromtimestamp(mtime)
                        conversation_files = list(project_path.glob("*.jsonl"))
                        cache_name = project_path.name
                        original_path = self._decode_project_name(cache_name)

                        projects.append(
                            {
                                "name": cache_name,
                                "original_path": original_path,
                                "cache_path": str(project_path),
                                "path": str(project_path),
                                "size_bytes": size_bytes,
                                "size_mb": round(size_bytes / (1024 * 1024), 2),
                                "last_modified": last_modified,
                                "conversation_count": len(conversation_files),
                                "source": source_label,
                            }
                        )
                    except (OSError, PermissionError) as e:
                        logger.warning("Error accessing project %s: %s", project_path, e)
                        continue
            except (OSError, PermissionError) as e:
                logger.error("Error listing projects in %s: %s", projects_dir, e)

        # Sort by size (largest first)
        projects.sort(key=lambda x: x["size_bytes"], reverse=True)

        return projects

    def export_project(self, project_path: str) -> tuple[bool, str]:
        """
        Export a project to the export directory

        Args:
            project_path: Full path to project directory

        Returns:
            Tuple of (success, message/error)
        """
        try:
            source = Path(project_path)
            if not source.exists():
                return False, f"Project not found: {project_path}"

            # Create export with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_name = f"{source.name}_{timestamp}"
            export_path = self.export_base_path / export_name

            # Copy entire project directory
            shutil.copytree(source, export_path)

            return True, f"Exported to: {export_path}"

        except (OSError, PermissionError, shutil.Error) as e:
            logger.error("Error exporting project %s: %s", project_path, e)
            return False, str(e)

    def delete_projects(self, project_paths: list[str], create_backup: bool = True) -> tuple[bool, str, dict]:
        """
        Delete multiple projects

        Args:
            project_paths: List of full paths to project directories
            create_backup: Whether to create backup before deletion

        Returns:
            Tuple of (success, message, details_dict)
        """
        deleted = []
        failed = []
        backed_up = []
        total_size_freed = 0

        for project_path in project_paths:
            try:
                source = Path(project_path)
                if not source.exists():
                    failed.append({"path": project_path, "error": "Not found"})
                    continue

                # Calculate size before deletion
                size = self.get_directory_size(source)

                # Create backup if requested
                if create_backup:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{source.name}_{timestamp}_backup"
                    backup_path = self.export_base_path / backup_name
                    shutil.copytree(source, backup_path)
                    backed_up.append(str(backup_path))

                # Delete the project
                shutil.rmtree(source)

                deleted.append(project_path)
                total_size_freed += size

            except (OSError, PermissionError, shutil.Error) as e:
                logger.error("Error deleting project %s: %s", project_path, e)
                failed.append({"path": project_path, "error": str(e)})

        # Build result message
        if not deleted and not failed:
            return False, "No projects specified", {}

        success = len(deleted) > 0
        size_mb = round(total_size_freed / (1024 * 1024), 1)

        message_parts = []
        if deleted:
            message_parts.append(f"Deleted {len(deleted)} project(s), freed {size_mb} MB")
        if failed:
            message_parts.append(f"{len(failed)} failed")

        details = {"deleted": deleted, "failed": failed, "backed_up": backed_up, "size_freed_mb": size_mb}

        return success, " | ".join(message_parts), details

    def get_all_folder_stats(self, use_cache: bool = False) -> dict[str, Any]:
        """Get size stats for all .claude subfolders across all managed directories.

        Args:
            use_cache: Whether to use cached values (default: False for fresh data)
        """
        folders: dict[str, Any] = {}

        folder_names = [
            "projects", "plugins", "file-history", "debug", "shell-snapshots",
            "session-env", "plans", "image-cache", "todos", "statsig",
            "ide", "telemetry", "paste-cache", "cache", "tasks",
        ]

        # Aggregate across all claude dirs
        for name in folder_names:
            total_size = 0
            for claude_d in self.claude_dirs:
                folder_path = claude_d / name
                if folder_path.exists():
                    total_size += self.get_directory_size(folder_path, use_cache=use_cache)
            folders[name] = {"size_bytes": total_size, "size_mb": round(total_size / (1024 * 1024), 2)}

        # Aggregate history.jsonl across dirs
        history_size = 0
        for claude_d in self.claude_dirs:
            history_file = claude_d / "history.jsonl"
            if history_file.exists():
                history_size += history_file.stat().st_size
        folders["history.jsonl"] = {"size_bytes": history_size, "size_mb": round(history_size / (1024 * 1024), 2)}

        # Aggregate plugins/cache
        plugins_size = 0
        for claude_d in self.claude_dirs:
            plugins_cache = claude_d / "plugins" / "cache"
            if plugins_cache.exists():
                plugins_size += self.get_directory_size(plugins_cache, use_cache=use_cache)
        folders["plugins/cache"] = {"size_bytes": plugins_size, "size_mb": round(plugins_size / (1024 * 1024), 2)}

        return folders
