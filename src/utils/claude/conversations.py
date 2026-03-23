"""
Claude Config Manager — conversation history management.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class _ConversationsMixin:
    """Conversation cleanup operations. Requires _ClaudeConfigBase attributes."""

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
