"""Git backup and restore operations."""

import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import DEFAULT_PROJECT_RETENTION_DAYS


class GitBackupMixin:
    """Mixin providing git backup and restore methods.

    Expects the following attributes on the composing class:
        config: ConfigManager instance
        logger: logging.Logger instance
        local_path: Path to local backup storage
        git_manager: GitManager instance
        notifier: NotificationManager | None
    """

    config: Any
    logger: logging.Logger
    local_path: Path
    git_manager: Any
    notifier: Any

    def backup_git(
        self, project_name: str, description: str | None = None, skip_if_exists_today: bool = False
    ) -> tuple[bool, str]:
        """Create a git bundle backup for a project

        Git bundles are portable, single-file archives containing git history.
        They can be cloned from or fetched into existing repositories.

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Check if it's a git repository
        if not self.git_manager.is_git_repo(str(project_path)):
            return False, f"Project '{project_name}' is not a git repository"

        # Check disk space
        local_backup_dir = self.local_path / "git" / project_name
        local_backup_dir.mkdir(parents=True, exist_ok=True)

        # Check if backup already exists today
        if skip_if_exists_today and self._has_backup_today(local_backup_dir, project_name):
            return True, "Skipped: git backup already exists for today"

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{project_name}_{timestamp}.bundle"
            local_backup_path = local_backup_dir / backup_name

            self.logger.info(f"Starting git bundle backup of project '{project_name}'")

            # Get current branch info
            git_status = self.git_manager.get_repo_status(str(project_path))
            current_branch = git_status.get("branch", "main")

            # Create git bundle with all refs
            cmd = ["git", "bundle", "create", str(local_backup_path), "--all"]
            result = subprocess.run(
                cmd, cwd=str(project_path), capture_output=True, text=True, timeout=self._get_timeout("git_bundle")
            )

            if result.returncode != 0:
                self.logger.debug(f"git bundle create stderr: {result.stderr}")
                raise RuntimeError("git bundle create failed (check logs for details)")

            # Get commit count and latest commit info
            commit_count = git_status.get("commit_count", 0)
            latest_commit = git_status.get("commits", [{}])[0] if git_status.get("commits") else {}

            # Finalize: permissions, metadata, sync, symlink, retention
            metadata_extra = {
                "backup_type": "git_bundle",
                "branch": current_branch,
                "commit_count": commit_count,
                "latest_commit_hash": latest_commit.get("hash", "unknown"),
                "latest_commit_message": latest_commit.get("message", "")[:100],
                "latest_commit_date": latest_commit.get("date", ""),
                "has_uncommitted_changes": git_status.get("is_dirty", False),
                "uncommitted_files": git_status.get("total_changes", 0),
            }
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "git",
                description or f"Git bundle backup - {current_branch}",
                f"git/{project_name}", "latest.bundle", retention_days,
                metadata_extra, lambda d, r: self._cleanup_old_backups(d, r, patterns=["*.bundle"]),
            )
            self.logger.info(
                f"Successfully backed up git history for '{project_name}' ({size_mb:.2f} MB, {commit_count} commits)"
            )

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "git", size_mb)

            return True, f"Git backup successful: {backup_name} ({size_mb:.2f} MB, {commit_count} commits)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to backup git for '{project_name}': {error_msg}")

            # Clean up partial bundle
            if "local_backup_path" in locals() and local_backup_path.exists():
                try:
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial bundle: {local_backup_path.name}")
                except OSError:
                    pass

            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "git", error_msg)

            return False, f"Git backup failed: {error_msg}"

    def backup_all_git(self, parallel: bool = True, skip_if_exists_today: bool = False) -> dict[str, tuple[bool, str]]:
        """Backup git history for all projects that are git repositories

        Args:
            parallel: If True, run backups in parallel
            skip_if_exists_today: Skip projects that already have a git backup today
        """
        results = {}

        # Filter to only git-enabled projects
        git_projects = []
        for name, project in self.config.get_all_projects().items():
            if self.git_manager.is_git_repo(project["path"]):
                git_projects.append(name)

        if not git_projects:
            return {"error": (False, "No git repositories found among projects")}

        if not parallel or len(git_projects) <= 1:
            for project_name in git_projects:
                results[project_name] = self.backup_git(project_name, skip_if_exists_today=skip_if_exists_today)
        else:
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel git backup of {len(git_projects)} projects")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_project = {
                    executor.submit(self.backup_git, project_name, None, skip_if_exists_today): project_name
                    for project_name in git_projects
                }

                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Git backup failed for '{project_name}': {e}")
                        results[project_name] = (False, f"Git backup failed: {e!s}")

        return results

    def restore_git(
        self, project_name: str, backup_file: str, target_path: str | None = None, mode: str = "clone"
    ) -> tuple[bool, str]:
        """Restore a git repository from a bundle backup

        Args:
            project_name: Name of the project
            backup_file: Bundle filename to restore from
            target_path: Target directory (optional, defaults to original location)
            mode: 'clone' (create new repo) or 'fetch' (update existing repo)
        """
        self._validate_identifier(project_name, "project name")
        self._validate_backup_filename(backup_file)
        backup_path = self.local_path / "git" / project_name / backup_file

        if not backup_path.exists():
            return False, f"Git backup file not found: {backup_file}"

        # Determine target path
        if target_path:
            restore_path = Path(target_path)
            self._validate_restore_target(restore_path)
        else:
            project = self.config.get_project(project_name)
            if not project:
                return False, f"Project '{project_name}' not found and no target path provided"
            restore_path = Path(project["path"])

        try:
            if mode == "clone":
                # Clone from bundle - creates new repository
                if restore_path.exists():
                    # Backup existing directory
                    backup_existing = (
                        restore_path.parent / f"{restore_path.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    )
                    shutil.move(str(restore_path), str(backup_existing))
                    self.logger.info(f"Existing directory moved to: {backup_existing}")

                cmd = ["git", "clone", str(backup_path), str(restore_path)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=self._get_timeout("git_clone"))

                if result.returncode != 0:
                    self.logger.debug(f"git clone stderr: {result.stderr}")
                    raise RuntimeError("git clone failed (check logs for details)")

                self.logger.info(f"Successfully cloned git backup to {restore_path}")
                return True, f"Git repository restored to {restore_path}"

            elif mode == "fetch":
                # Fetch from bundle into existing repository
                if not restore_path.exists() or not self.git_manager.is_git_repo(str(restore_path)):
                    return False, "Target path is not a git repository. Use mode='clone' instead."

                # Verify the bundle
                verify_cmd = ["git", "bundle", "verify", str(backup_path)]
                verify_result = subprocess.run(
                    verify_cmd,
                    cwd=str(restore_path),
                    capture_output=True,
                    text=True,
                    timeout=self._get_timeout("git_verify"),
                )

                if verify_result.returncode != 0:
                    self.logger.debug(f"git bundle verify stderr: {verify_result.stderr}")
                    return False, "Bundle verification failed (check logs for details)"

                # Fetch from bundle
                fetch_cmd = ["git", "fetch", str(backup_path), "*:*"]
                fetch_result = subprocess.run(
                    fetch_cmd,
                    cwd=str(restore_path),
                    capture_output=True,
                    text=True,
                    timeout=self._get_timeout("git_clone"),
                )

                if fetch_result.returncode != 0:
                    # Try alternative fetch syntax
                    fetch_cmd = ["git", "pull", str(backup_path), "HEAD"]
                    fetch_result = subprocess.run(
                        fetch_cmd,
                        cwd=str(restore_path),
                        capture_output=True,
                        text=True,
                        timeout=self._get_timeout("git_clone"),
                    )

                    if fetch_result.returncode != 0:
                        self.logger.debug(f"git fetch stderr: {fetch_result.stderr}")
                        raise RuntimeError("git fetch failed (check logs for details)")

                self.logger.info(f"Successfully fetched git backup into {restore_path}")
                return True, f"Git history fetched into existing repository at {restore_path}"

            else:
                return False, f"Invalid mode: {mode}. Use 'clone' or 'fetch'."

        except Exception as e:
            self.logger.error(f"Failed to restore git backup: {e!s}")
            return False, f"Git restore failed: {e!s}"
