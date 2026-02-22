"""
Claude Configuration Manager
Manages Claude Code configuration, project history cleanup, and MCP servers
"""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ClaudeConfigManager:
    """Manages Claude Code configuration and cleanup operations"""

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

    def invalidate_cache(self, directory: Path | None = None):
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

    def get_stats(self, use_cache: bool = False) -> dict[str, Any]:
        """
        Get overall Claude configuration statistics

        Args:
            use_cache: Whether to use cached values (default: False for fresh data)

        Returns:
            Dictionary with size, count, and health metrics
        """
        if not self.claude_dir.exists():
            return {
                "exists": False,
                "total_size_bytes": 0,
                "total_size_mb": 0.0,
                "projects_count": 0,
                "projects_size_mb": 0.0,
                "health": "unknown",
                "largest_project": None,
            }

        # Calculate total .claude directory size - use fresh data by default
        total_size = self.get_directory_size(self.claude_dir, use_cache=use_cache)

        # Calculate projects directory size and count
        projects_size = 0
        projects_count = 0
        if self.projects_dir.exists():
            projects_size = self.get_directory_size(self.projects_dir, use_cache=use_cache)
            projects_count = len(list(self.projects_dir.iterdir()))

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
        }

    def list_projects(self) -> list[dict[str, Any]]:
        """
        List all Claude projects with their sizes and metadata

        Returns:
            List of project dictionaries with path, size, date info
        """
        if not self.projects_dir.exists():
            return []

        projects: list[dict[str, Any]] = []

        try:
            for project_path in self.projects_dir.iterdir():
                if not project_path.is_dir():
                    continue

                try:
                    size_bytes = self.get_directory_size(project_path)

                    # Get last modified time
                    mtime = project_path.stat().st_mtime
                    last_modified = datetime.fromtimestamp(mtime)

                    # Count conversation files
                    conversation_files = list(project_path.glob("*.jsonl"))

                    # Try to find the actual project path by testing combinations
                    # Cache: "-home-user-my-project" could be:
                    #   /home/user/my-project ✓
                    #   /home/user/my/project ✗
                    cache_name = project_path.name

                    # Start with simple replacement
                    original_path = cache_name.replace("-", "/")

                    # If that path doesn't exist, try to find the correct one
                    if not Path(original_path).exists():
                        # Try different combinations by treating consecutive segments as one dir name
                        parts = cache_name.split("-")

                        # Try combining last 2, 3, 4 parts with dashes
                        for num_parts in range(2, min(5, len(parts))):
                            # Combine last num_parts parts with dashes
                            test_parts = [*parts[:-num_parts], "-".join(parts[-num_parts:])]
                            test_path = "/".join(test_parts)

                            if Path(test_path).exists():
                                original_path = test_path
                                break

                    projects.append(
                        {
                            "name": cache_name,
                            "original_path": original_path,  # The actual project path
                            "cache_path": str(project_path),  # The cache location
                            "path": str(project_path),  # Keep for compatibility
                            "size_bytes": size_bytes,
                            "size_mb": round(size_bytes / (1024 * 1024), 2),
                            "last_modified": last_modified,
                            "conversation_count": len(conversation_files),
                        }
                    )
                except (OSError, PermissionError) as e:
                    logger.warning(f"Error accessing project {project_path}: {e}")
                    continue
        except (OSError, PermissionError) as e:
            logger.error(f"Error listing projects: {e}")
            return []

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

        except Exception as e:
            logger.error(f"Error exporting project {project_path}: {e}")
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

            except Exception as e:
                logger.error(f"Error deleting project {project_path}: {e}")
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

    def get_mcp_servers(self) -> tuple[bool, list[dict], str]:
        """
        Get list of configured MCP servers

        Returns:
            Tuple of (success, servers_list, error_message)
        """
        if not self.mcp_config_path.exists():
            return True, [], ""

        try:
            with open(self.mcp_config_path) as f:
                config = json.load(f)

            servers = []
            mcp_servers = config.get("mcpServers", {})

            for name, settings in mcp_servers.items():
                servers.append(
                    {
                        "name": name,
                        "command": settings.get("command", ""),
                        "args": settings.get("args", []),
                        "env": settings.get("env", {}),
                        "disabled": settings.get("disabled", False),
                    }
                )

            return True, servers, ""

        except json.JSONDecodeError as e:
            return False, [], f"Invalid JSON: {e}"
        except Exception as e:
            return False, [], f"Error reading MCP config: {e}"

    def save_mcp_servers(self, servers: list[dict]) -> tuple[bool, str]:
        """
        Save MCP servers configuration

        Args:
            servers: List of server dictionaries

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Read existing config or create new
            if self.mcp_config_path.exists():
                with open(self.mcp_config_path) as f:
                    config = json.load(f)
            else:
                config = {}

            # Rebuild mcpServers section
            mcp_servers = {}
            for server in servers:
                server_config = {
                    "command": server["command"],
                }
                if server.get("args"):
                    server_config["args"] = server["args"]
                if server.get("env"):
                    server_config["env"] = server["env"]
                if server.get("disabled"):
                    server_config["disabled"] = True

                mcp_servers[server["name"]] = server_config

            config["mcpServers"] = mcp_servers

            # Create backup
            if self.mcp_config_path.exists():
                backup_path = self.mcp_config_path.with_suffix(".json.backup")
                shutil.copy2(self.mcp_config_path, backup_path)

            # Write updated config
            with open(self.mcp_config_path, "w") as f:
                json.dump(config, f, indent=2)

            return True, ""

        except Exception as e:
            logger.error(f"Error saving MCP config: {e}")
            return False, str(e)

    def add_mcp_server(
        self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None
    ) -> tuple[bool, str]:
        """
        Add a new MCP server

        Args:
            name: Server name
            command: Command to run
            args: Command arguments
            env: Environment variables

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Check if name already exists
        if any(s["name"] == name for s in servers):
            return False, f"Server '{name}' already exists"

        # Add new server
        new_server = {"name": name, "command": command, "args": args or [], "env": env or {}, "disabled": False}

        servers.append(new_server)
        return self.save_mcp_servers(servers)

    def delete_mcp_server(self, name: str) -> tuple[bool, str]:
        """
        Delete an MCP server

        Args:
            name: Server name to delete

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Filter out the server
        servers = [s for s in servers if s["name"] != name]
        return self.save_mcp_servers(servers)

    def update_mcp_server(self, old_name: str, updated_server: dict) -> tuple[bool, str]:
        """
        Update an existing MCP server

        Args:
            old_name: Current server name
            updated_server: Updated server dictionary

        Returns:
            Tuple of (success, error_message)
        """
        success, servers, error = self.get_mcp_servers()
        if not success:
            return False, error

        # Find and update the server
        found = False
        for i, server in enumerate(servers):
            if server["name"] == old_name:
                servers[i] = updated_server
                found = True
                break

        if not found:
            return False, f"Server '{old_name}' not found"

        return self.save_mcp_servers(servers)

    def keep_last_n_conversations(self, project_path: str, keep_count: int = 3) -> tuple[bool, str, dict]:
        """
        Keep only the last N conversations in a project (both .jsonl files and UUID directories)

        Args:
            project_path: Full path to project directory
            keep_count: Number of recent conversations to keep

        Returns:
            Tuple of (success, message, details_dict)
        """
        try:
            source = Path(project_path)
            if not source.exists():
                return False, f"Project not found: {project_path}", {}

            # Get all .jsonl files (conversation files)
            conversation_files = sorted(source.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)

            # Get all UUID directories (conversation data directories)
            # Skip protected dirs (e.g. memory/) that Claude Code uses for persistent data
            conversation_dirs = sorted(
                [
                    d
                    for d in source.iterdir()
                    if d.is_dir() and d.name not in self.PROTECTED_DIRS and self.UUID_PATTERN.match(d.name)
                ],
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )

            total_files = len(conversation_files)
            total_dirs = len(conversation_dirs)

            size_freed = 0
            deleted_files = 0
            deleted_dirs = 0

            # Delete old .jsonl files
            if total_files > keep_count:
                files_to_delete = conversation_files[keep_count:]
                for file_path in files_to_delete:
                    size_freed += file_path.stat().st_size
                    file_path.unlink()
                    deleted_files += 1

            # Delete old UUID directories
            if total_dirs > keep_count:
                dirs_to_delete = conversation_dirs[keep_count:]
                for dir_path in dirs_to_delete:
                    size_freed += self.get_directory_size(dir_path)
                    shutil.rmtree(dir_path)
                    deleted_dirs += 1

            if deleted_files == 0 and deleted_dirs == 0:
                return (
                    True,
                    f"Only {max(total_files, total_dirs)} conversation(s), nothing to delete",
                    {
                        "total": max(total_files, total_dirs),
                        "kept": keep_count,
                        "deleted": 0,
                        "deleted_files": 0,
                        "deleted_dirs": 0,
                        "size_freed_mb": 0,
                    },
                )

            size_mb = round(size_freed / (1024 * 1024), 2)

            return (
                True,
                f"Kept {keep_count}, deleted {deleted_files} files + {deleted_dirs} dirs",
                {
                    "total": max(total_files, total_dirs),
                    "kept": keep_count,
                    "deleted": deleted_files + deleted_dirs,
                    "deleted_files": deleted_files,
                    "deleted_dirs": deleted_dirs,
                    "size_freed_mb": size_mb,
                },
            )

        except Exception as e:
            logger.error(f"Error keeping last N conversations for {project_path}: {e}")
            return False, str(e), {}

    def keep_last_n_all_projects(self, keep_count: int = 3) -> tuple[bool, str, dict]:
        """
        Keep only last N conversations for all projects

        Args:
            keep_count: Number of recent conversations to keep per project

        Returns:
            Tuple of (success, message, details_dict)
        """
        projects = self.list_projects()

        total_deleted = 0
        total_size_freed = 0
        projects_cleaned = 0

        for project in projects:
            success, _message, details = self.keep_last_n_conversations(project["path"], keep_count)

            if success and details.get("deleted", 0) > 0:
                projects_cleaned += 1
                total_deleted += details["deleted"]
                total_size_freed += details["size_freed_mb"]

        return (
            True,
            f"Cleaned {projects_cleaned} projects",
            {
                "projects_cleaned": projects_cleaned,
                "conversations_deleted": total_deleted,
                "size_freed_mb": round(total_size_freed, 2),
            },
        )

    def clean_old_conversations(self, project_path: str, max_age_days: int = 7) -> tuple[bool, str, dict]:
        """
        Delete conversations older than max_age_days in a project (age-based cleanup).
        Complements keep_last_n_conversations which is count-based.

        Args:
            project_path: Full path to project directory
            max_age_days: Delete conversations older than this many days

        Returns:
            Tuple of (success, message, details_dict)
        """
        try:
            source = Path(project_path)
            if not source.exists():
                return False, f"Project not found: {project_path}", {}

            now = datetime.now().timestamp()
            cutoff_time = now - (max_age_days * 86400)

            size_freed = 0
            deleted_files = 0
            deleted_dirs = 0

            # Delete old .jsonl files
            for jsonl_file in source.glob("*.jsonl"):
                if jsonl_file.stat().st_mtime < cutoff_time:
                    size_freed += jsonl_file.stat().st_size
                    jsonl_file.unlink()
                    deleted_files += 1

            # Delete old UUID session directories (skip protected dirs like memory/)
            for subdir in source.iterdir():
                if not subdir.is_dir():
                    continue
                if subdir.name in self.PROTECTED_DIRS:
                    continue
                if not self.UUID_PATTERN.match(subdir.name):
                    continue
                if subdir.stat().st_mtime < cutoff_time:
                    size_freed += self.get_directory_size(subdir)
                    shutil.rmtree(subdir)
                    deleted_dirs += 1

            if deleted_files == 0 and deleted_dirs == 0:
                return (
                    True,
                    f"No conversations older than {max_age_days} days",
                    {"deleted": 0, "deleted_files": 0, "deleted_dirs": 0, "size_freed_mb": 0},
                )

            size_mb = round(size_freed / (1024 * 1024), 2)

            return (
                True,
                f"Deleted {deleted_files} files + {deleted_dirs} dirs older than {max_age_days}d",
                {
                    "deleted": deleted_files + deleted_dirs,
                    "deleted_files": deleted_files,
                    "deleted_dirs": deleted_dirs,
                    "size_freed_mb": size_mb,
                },
            )

        except Exception as e:
            logger.error(f"Error cleaning old conversations for {project_path}: {e}")
            return False, str(e), {}

    def clean_old_conversations_all_projects(self, max_age_days: int = 7) -> tuple[bool, str, dict]:
        """
        Delete conversations older than max_age_days across all projects.

        Args:
            max_age_days: Delete conversations older than this many days

        Returns:
            Tuple of (success, message, details_dict)
        """
        projects = self.list_projects()

        total_deleted = 0
        total_size_freed = 0
        projects_cleaned = 0

        for project in projects:
            success, _message, details = self.clean_old_conversations(project["path"], max_age_days)

            if success and details.get("deleted", 0) > 0:
                projects_cleaned += 1
                total_deleted += details["deleted"]
                total_size_freed += details["size_freed_mb"]

        # Invalidate cache after bulk cleanup
        if projects_cleaned > 0:
            self.invalidate_cache()

        return (
            True,
            f"Cleaned {projects_cleaned} projects (>{max_age_days}d)",
            {
                "projects_cleaned": projects_cleaned,
                "conversations_deleted": total_deleted,
                "size_freed_mb": round(total_size_freed, 2),
            },
        )

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

    def get_all_folder_stats(self, use_cache: bool = False) -> dict[str, Any]:
        """Get size stats for all .claude subfolders

        Args:
            use_cache: Whether to use cached values (default: False for fresh data)
        """
        folders = {}

        # Define all known folders
        folder_names = [
            "projects",
            "plugins",
            "file-history",
            "debug",
            "shell-snapshots",
            "session-env",
            "plans",
            "image-cache",
            "todos",
            "statsig",
            "ide",
            "telemetry",
            "paste-cache",
            "cache",
            "tasks",
        ]

        for name in folder_names:
            folder_path = self.claude_dir / name
            if folder_path.exists():
                size = self.get_directory_size(folder_path, use_cache=use_cache)
                folders[name] = {"size_bytes": size, "size_mb": round(size / (1024 * 1024), 2)}
            else:
                folders[name] = {"size_bytes": 0, "size_mb": 0}

        # Add history.jsonl
        history_file = self.claude_dir / "history.jsonl"
        if history_file.exists():
            size = history_file.stat().st_size
            folders["history.jsonl"] = {"size_bytes": size, "size_mb": round(size / (1024 * 1024), 2)}
        else:
            folders["history.jsonl"] = {"size_bytes": 0, "size_mb": 0}

        # Add plugins/cache separately
        plugins_cache = self.claude_dir / "plugins" / "cache"
        if plugins_cache.exists():
            size = self.get_directory_size(plugins_cache, use_cache=use_cache)
            folders["plugins/cache"] = {"size_bytes": size, "size_mb": round(size / (1024 * 1024), 2)}
        else:
            folders["plugins/cache"] = {"size_bytes": 0, "size_mb": 0}

        return folders

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

                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

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


class BackupCleanupManager:
    """Manages cleanup of old backup files on local and sync storage locations"""

    def __init__(self, local_path: Path | None = None, sync_path: Path | None = None):
        self.local_path = local_path or Path.home() / "backups" / "qm"
        self.sync_path = sync_path
        self.backup_base_path = self.local_path
        self.logger = logging.getLogger("BackupCleanupManager")

    def _get_path(self, location: str) -> Path | None:
        """Get the path for a given location (returns None if sync not configured)"""
        if location == "sync":
            return self.sync_path
        return self.local_path

    def get_backup_stats(self, location: str = "local") -> dict[str, Any]:
        """
        Get statistics about all backups for a specific location

        Args:
            location: 'local' or 'sync'
        """
        backup_path = self._get_path(location)

        stats: dict[str, Any] = {
            "exists": False,
            "location": location,
            "path": str(backup_path),
            "total_size_mb": 0,
            "projects": {"count": 0, "size_mb": 0, "files": 0},
            "databases": {"count": 0, "size_mb": 0, "files": 0},
            "old_30d": {"size_mb": 0, "files": 0},
            "old_60d": {"size_mb": 0, "files": 0},
            "old_90d": {"size_mb": 0, "files": 0},
        }

        if backup_path is None or not backup_path.exists():
            return stats

        stats["exists"] = True
        now = datetime.now().timestamp()

        # Check projects
        projects_dir = backup_path / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir():
                    stats["projects"]["count"] += 1
                    for backup_file in project_dir.glob("*.tar.gz"):
                        size = backup_file.stat().st_size
                        mtime = backup_file.stat().st_mtime
                        age_days = (now - mtime) / 86400

                        stats["projects"]["size_mb"] += size / (1024 * 1024)
                        stats["projects"]["files"] += 1
                        stats["total_size_mb"] += size / (1024 * 1024)

                        if age_days > 30:
                            stats["old_30d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_30d"]["files"] += 1
                        if age_days > 60:
                            stats["old_60d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_60d"]["files"] += 1
                        if age_days > 90:
                            stats["old_90d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_90d"]["files"] += 1

        # Check databases
        databases_dir = backup_path / "databases"
        if databases_dir.exists():
            for db_dir in databases_dir.iterdir():
                if db_dir.is_dir():
                    stats["databases"]["count"] += 1
                    for backup_file in db_dir.glob("*.sql.gz"):
                        size = backup_file.stat().st_size
                        mtime = backup_file.stat().st_mtime
                        age_days = (now - mtime) / 86400

                        stats["databases"]["size_mb"] += size / (1024 * 1024)
                        stats["databases"]["files"] += 1
                        stats["total_size_mb"] += size / (1024 * 1024)

                        if age_days > 30:
                            stats["old_30d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_30d"]["files"] += 1
                        if age_days > 60:
                            stats["old_60d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_60d"]["files"] += 1
                        if age_days > 90:
                            stats["old_90d"]["size_mb"] += size / (1024 * 1024)
                            stats["old_90d"]["files"] += 1

        # Round all values
        for key in ["total_size_mb"]:
            stats[key] = round(stats[key], 2)
        for section in ["projects", "databases", "old_30d", "old_60d", "old_90d"]:
            if "size_mb" in stats[section]:
                stats[section]["size_mb"] = round(stats[section]["size_mb"], 2)

        return stats

    def clean_old_backups(
        self, max_age_days: int, backup_type: str = "all", keep_minimum: int = 15, location: str = "local"
    ) -> tuple[bool, str, dict]:
        """
        Clean backups older than specified days

        Args:
            max_age_days: Delete backups older than this many days
            backup_type: 'projects', 'databases', or 'all'
            keep_minimum: Always keep at least this many backups per project/database
            location: 'local', 'sync', or 'both'
        """
        # Handle 'both' locations
        if location == "both":
            local_result = self.clean_old_backups(max_age_days, backup_type, keep_minimum, "local")
            sync_result = self.clean_old_backups(max_age_days, backup_type, keep_minimum, "sync")

            total_deleted = local_result[2].get("deleted", 0) + sync_result[2].get("deleted", 0)
            total_freed = local_result[2].get("size_freed_mb", 0) + sync_result[2].get("size_freed_mb", 0)

            return (
                True,
                f"Deleted {total_deleted} backups from both locations, freed {total_freed:.2f} MB",
                {
                    "deleted": total_deleted,
                    "size_freed_mb": total_freed,
                    "local": local_result[2],
                    "sync": sync_result[2],
                },
            )

        backup_path = self._get_path(location)

        if backup_path is None or not backup_path.exists():
            return True, f"No backup directory found at {location}", {"deleted": 0, "size_freed_mb": 0}

        now = datetime.now().timestamp()
        cutoff_time = now - (max_age_days * 86400)

        deleted_count = 0
        size_freed = 0
        deleted_files = []

        dirs_to_check = []
        if backup_type in ["projects", "all"]:
            projects_dir = backup_path / "projects"
            if projects_dir.exists():
                for d in projects_dir.iterdir():
                    if d.is_dir():
                        dirs_to_check.append((d, "*.tar.gz"))

        if backup_type in ["databases", "all"]:
            databases_dir = backup_path / "databases"
            if databases_dir.exists():
                for d in databases_dir.iterdir():
                    if d.is_dir():
                        dirs_to_check.append((d, "*.sql.gz"))

        for backup_dir, pattern in dirs_to_check:
            # Get all backup files sorted by date (newest first)
            backup_files = sorted(backup_dir.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)

            # Keep at least keep_minimum backups
            files_to_consider = backup_files[keep_minimum:] if len(backup_files) > keep_minimum else []

            for backup_file in files_to_consider:
                mtime = backup_file.stat().st_mtime
                if mtime < cutoff_time:
                    try:
                        size = backup_file.stat().st_size
                        backup_file.unlink()

                        # Also delete metadata file if exists
                        metadata_file = backup_file.parent / backup_file.name.replace(".tar.gz", ".json").replace(
                            ".sql.gz", ".json"
                        )
                        if metadata_file.exists():
                            metadata_file.unlink()

                        deleted_count += 1
                        size_freed += size
                        deleted_files.append(backup_file.name)
                        self.logger.info(f"Deleted old backup: {backup_file}")

                    except Exception as e:
                        self.logger.error(f"Failed to delete {backup_file}: {e}")

        size_mb = round(size_freed / (1024 * 1024), 2)

        return (
            True,
            f"Deleted {deleted_count} backups older than {max_age_days} days, freed {size_mb} MB",
            {
                "deleted": deleted_count,
                "size_freed_mb": size_mb,
                "files": deleted_files[:20],  # Limit to first 20 for display
            },
        )

    def get_backup_details(self, location: str = "local") -> list[dict]:
        """
        Get detailed list of all backups grouped by project/database

        Args:
            location: 'local' or 'sync'
        """
        details: list[dict[str, Any]] = []
        backup_path = self._get_path(location)

        if backup_path is None or not backup_path.exists():
            return details

        now = datetime.now().timestamp()

        # Projects
        projects_dir = backup_path / "projects"
        if projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir()):
                if project_dir.is_dir():
                    backups = list(project_dir.glob("*.tar.gz"))
                    if backups:
                        total_size = sum(f.stat().st_size for f in backups)
                        oldest = min(backups, key=lambda x: x.stat().st_mtime)
                        newest = max(backups, key=lambda x: x.stat().st_mtime)

                        oldest_age = int((now - oldest.stat().st_mtime) / 86400)
                        newest_age = int((now - newest.stat().st_mtime) / 86400)

                        details.append(
                            {
                                "name": project_dir.name,
                                "type": "project",
                                "count": len(backups),
                                "size_mb": round(total_size / (1024 * 1024), 2),
                                "oldest_days": oldest_age,
                                "newest_days": newest_age,
                            }
                        )

        # Databases
        databases_dir = backup_path / "databases"
        if databases_dir.exists():
            for db_dir in sorted(databases_dir.iterdir()):
                if db_dir.is_dir():
                    backups = list(db_dir.glob("*.sql.gz"))
                    if backups:
                        total_size = sum(f.stat().st_size for f in backups)
                        oldest = min(backups, key=lambda x: x.stat().st_mtime)
                        newest = max(backups, key=lambda x: x.stat().st_mtime)

                        oldest_age = int((now - oldest.stat().st_mtime) / 86400)
                        newest_age = int((now - newest.stat().st_mtime) / 86400)

                        details.append(
                            {
                                "name": db_dir.name,
                                "type": "database",
                                "count": len(backups),
                                "size_mb": round(total_size / (1024 * 1024), 2),
                                "oldest_days": oldest_age,
                                "newest_days": newest_age,
                            }
                        )

        # Sort by size descending
        details.sort(key=lambda x: x["size_mb"], reverse=True)

        return details
