"""Background backup manager for async backup execution"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.backup_engine import BackupEngine
    from core.config_manager import ConfigManager


class BackupStatus(Enum):
    """Backup task status"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class BackupTask:
    """Represents a backup task"""

    task_id: str
    task_type: str  # 'project', 'database', 'all-projects', 'all-databases'
    target: str  # Project/database name or 'all'
    status: BackupStatus = BackupStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    result_message: str | None = None
    progress: int = 0  # 0-100


DEFAULT_TASK_MAX_AGE_HOURS = 24


class BackgroundBackupManager:
    """Manages background backup execution and scheduling"""

    def __init__(self, backup_engine: "BackupEngine", config_manager: "ConfigManager"):
        """Initialize the background backup manager

        Args:
            backup_engine: BackupEngine instance
            config_manager: ConfigManager instance
        """
        self.backup_engine = backup_engine
        self.config = config_manager
        self.logger = logging.getLogger("BackgroundBackupManager")

        # Task tracking
        self.tasks: dict[str, BackupTask] = {}
        self.active_threads: list[threading.Thread] = []
        self._lock = threading.Lock()

        # Callbacks for UI updates
        self.on_task_update: Callable[[BackupTask], None] | None = None

        # Last run tracking
        self.last_run_file = Path(__file__).parent.parent.parent / "config" / ".last_backup_run"

    def _get_last_run_time(self, backup_type: str) -> datetime | None:
        """Get the last time a backup type was run

        Args:
            backup_type: 'projects' or 'databases'

        Returns:
            Last run datetime or None
        """
        if not self.last_run_file.exists():
            return None

        try:
            with open(self.last_run_file) as f:
                data = json.load(f)
                last_run_str = data.get(backup_type)
                if last_run_str:
                    return datetime.fromisoformat(last_run_str)
        except Exception as e:
            self.logger.warning(f"Could not read last run time: {e}")

        return None

    def _update_last_run_time(self, backup_type: str) -> None:
        """Update the last run time for a backup type

        Args:
            backup_type: 'projects' or 'databases'
        """
        try:
            # Load existing data
            data = {}
            if self.last_run_file.exists():
                with open(self.last_run_file) as f:
                    data = json.load(f)

            # Update with current time
            data[backup_type] = datetime.now().isoformat()

            # Save
            with open(self.last_run_file, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            self.logger.warning(f"Could not update last run time: {e}")

    def check_overdue_backups(self) -> dict[str, Any]:
        """Check if any backups are overdue

        Returns:
            Dictionary with overdue status and details
        """
        overdue: dict[str, Any] = {"projects": False, "databases": False, "details": []}

        # Check projects
        last_projects = self._get_last_run_time("projects")
        if last_projects is None or (datetime.now() - last_projects) > timedelta(hours=24):
            overdue["projects"] = True
            overdue["details"].append(
                {
                    "type": "projects",
                    "last_run": last_projects.isoformat() if last_projects else "Never",
                    "overdue_hours": int((datetime.now() - last_projects).total_seconds() / 3600)
                    if last_projects
                    else None,
                }
            )

        # Check databases
        last_databases = self._get_last_run_time("databases")
        if last_databases is None or (datetime.now() - last_databases) > timedelta(hours=24):
            overdue["databases"] = True
            overdue["details"].append(
                {
                    "type": "databases",
                    "last_run": last_databases.isoformat() if last_databases else "Never",
                    "overdue_hours": int((datetime.now() - last_databases).total_seconds() / 3600)
                    if last_databases
                    else None,
                }
            )

        return overdue

    def run_overdue_backups(self, force: bool = False) -> list[str]:
        """Run any overdue backups in background

        Args:
            force: Force run even if not overdue

        Returns:
            List of task IDs created
        """
        task_ids = []

        if force:
            # Force run both
            task_ids.append(self.schedule_backup("all-projects", "all"))
            task_ids.append(self.schedule_backup("all-databases", "all"))
        else:
            # Check what's overdue
            overdue = self.check_overdue_backups()

            if overdue["projects"]:
                task_ids.append(self.schedule_backup("all-projects", "all"))
                self.logger.info("Scheduled overdue project backups")

            if overdue["databases"]:
                task_ids.append(self.schedule_backup("all-databases", "all"))
                self.logger.info("Scheduled overdue database backups")

        return task_ids

    def schedule_backup(self, task_type: str, target: str) -> str:
        """Schedule a backup to run in background

        Args:
            task_type: 'project', 'database', 'all-projects', 'all-databases'
            target: Project/database name or 'all'

        Returns:
            Task ID
        """
        task_id = f"{task_type}_{target}_{int(time.time())}"

        with self._lock:
            # Create task
            task = BackupTask(task_id=task_id, task_type=task_type, target=target, status=BackupStatus.PENDING)
            self.tasks[task_id] = task

        # Start background thread
        thread = threading.Thread(target=self._execute_backup, args=(task_id,), daemon=True)
        thread.start()
        self.active_threads.append(thread)

        self.logger.info(f"Scheduled backup task: {task_id}")
        return task_id

    def _execute_backup(self, task_id: str):
        """Execute a backup task in background

        Args:
            task_id: Task ID to execute
        """
        task = self.tasks.get(task_id)
        if not task:
            return

        try:
            # Update status
            with self._lock:
                task.status = BackupStatus.RUNNING
                task.started_at = datetime.now()
                task.progress = 10
            self._notify_update(task)

            # Execute backup based on type
            if task.task_type == "all-projects":
                task.progress = 30
                self._notify_update(task)
                # Skip if today's backup already exists to prevent duplicates on restart
                results = self.backup_engine.backup_all_projects(parallel=True, skip_if_exists_today=True)

                # Check results
                success_count = sum(1 for success, _ in results.values() if success)
                total_count = len(results)

                if success_count == total_count:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = f"All {total_count} projects backed up successfully"
                elif success_count > 0:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = f"{success_count}/{total_count} projects backed up successfully"
                else:
                    task.status = BackupStatus.FAILED
                    task.error_message = "All project backups failed"

                # Update last run time
                self._update_last_run_time("projects")

            elif task.task_type == "all-databases":
                task.progress = 30
                self._notify_update(task)
                # Skip if today's backup already exists to prevent duplicates on restart
                results = self.backup_engine.backup_all_databases(parallel=True, skip_if_exists_today=True)

                # Check results
                success_count = sum(1 for success, _ in results.values() if success)
                total_count = len(results)

                if success_count == total_count:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = f"All {total_count} databases backed up successfully"
                elif success_count > 0:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = f"{success_count}/{total_count} databases backed up successfully"
                else:
                    task.status = BackupStatus.FAILED
                    task.error_message = "All database backups failed"

                # Update last run time
                self._update_last_run_time("databases")

            elif task.task_type == "project":
                task.progress = 50
                self._notify_update(task)
                success, message = self.backup_engine.backup_project(task.target)

                if success:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = message
                else:
                    task.status = BackupStatus.FAILED
                    task.error_message = message

            elif task.task_type == "database":
                task.progress = 50
                self._notify_update(task)
                success, message = self.backup_engine.backup_database(task.target)

                if success:
                    task.status = BackupStatus.COMPLETED
                    task.result_message = message
                else:
                    task.status = BackupStatus.FAILED
                    task.error_message = message

            # Final update
            task.progress = 100
            task.completed_at = datetime.now()

        except Exception as e:
            self.logger.error(f"Backup task {task_id} failed: {e}", exc_info=True)
            task.status = BackupStatus.FAILED
            task.error_message = str(e)
            task.completed_at = datetime.now()

        finally:
            self._notify_update(task)

    def _notify_update(self, task: BackupTask) -> None:
        """Notify callback of task update

        Args:
            task: Updated task
        """
        if self.on_task_update:
            try:
                self.on_task_update(task)
            except Exception as e:
                self.logger.warning(f"Error in task update callback: {e}")

    def get_task_status(self, task_id: str) -> BackupTask | None:
        """Get status of a task

        Args:
            task_id: Task ID

        Returns:
            BackupTask or None
        """
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> list[BackupTask]:
        """Get all tasks

        Returns:
            List of all backup tasks
        """
        return list(self.tasks.values())

    def get_running_tasks(self) -> list[BackupTask]:
        """Get currently running tasks

        Returns:
            List of running tasks
        """
        return [task for task in self.tasks.values() if task.status == BackupStatus.RUNNING]

    def cleanup_old_tasks(self, max_age_hours: int = DEFAULT_TASK_MAX_AGE_HOURS) -> int:
        """Remove old completed tasks from memory

        Args:
            max_age_hours: Maximum age of tasks to keep
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        with self._lock:
            to_remove = []
            for task_id, task in self.tasks.items():
                if task.completed_at and task.completed_at < cutoff:
                    to_remove.append(task_id)

            for task_id in to_remove:
                del self.tasks[task_id]

            if to_remove:
                self.logger.info(f"Cleaned up {len(to_remove)} old tasks")

            return len(to_remove)
