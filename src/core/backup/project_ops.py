"""Project backup and restore operations."""

import fnmatch
import json
import logging
import os
import re
import shutil
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config_manager import ConfigManager

# Import constants from the package
from .constants import DEFAULT_PROJECT_RETENTION_DAYS, ESTIMATED_COMPRESSION_RATIO, WHITELISTED_DOTFILES


class ProjectBackupMixin:
    """Mixin providing project backup and restore methods.

    Expects the following attributes on the composing class:
        config: ConfigManager instance
        logger: logging.Logger instance
        local_path: Path to local backup storage
        sync_path: Path | None to secondary storage
        git_manager: GitManager instance
        notifier: NotificationManager | None
    """

    config: ConfigManager
    logger: logging.Logger
    local_path: Path
    sync_path: Path | None
    git_manager: Any
    notifier: Any

    def backup_project(
        self,
        project_name: str,
        description: str | None = None,
        incremental: bool = False,
        skip_if_exists_today: bool = False,
    ) -> tuple[bool, str]:
        """Backup a single project

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            incremental: Whether to create incremental backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        if not project.get("backup", {}).get("enabled", True):
            return False, f"Backup disabled for project '{project_name}'"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Check if backup already exists today
        local_backup_dir = self.local_path / "projects" / project_name
        if skip_if_exists_today and self._has_backup_today(local_backup_dir, project_name):
            return True, "Skipped: backup already exists for today"

        # Check disk space before starting backup
        # Merge project-specific excludes with global excludes from settings
        project_excludes = project.get("exclude", [])
        global_excludes = self.config.get_global_excludes()
        exclude_patterns = list(set(project_excludes + global_excludes))
        estimated_size = self._estimate_project_size(project_path, exclude_patterns)

        # Estimate compressed size (tar.gz typically achieves 60-80% compression for code)
        estimated_compressed_size = int(estimated_size * ESTIMATED_COMPRESSION_RATIO)

        space_ok, space_msg = self._check_disk_space(local_backup_dir, estimated_compressed_size)

        if not space_ok:
            self.logger.error(f"Disk space check failed for project '{project_name}': {space_msg}")
            return False, space_msg

        self.logger.debug(f"Disk space check passed: {space_msg}")

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{project_name}_{timestamp}.tar.gz"

            # Local backup path
            local_backup_dir.mkdir(parents=True, exist_ok=True)
            local_backup_path = local_backup_dir / backup_name

            # Get exclusion patterns (already computed above, but refresh in case)
            # This ensures we use the merged project + global excludes

            # Determine if incremental backup is possible
            last_full_backup = None
            snapshot_file = None
            if incremental:
                # Look for the last full backup
                full_backups = sorted(
                    [f for f in local_backup_dir.glob(f"{project_name}_*_full.tar.gz") if not f.is_symlink()],
                    key=lambda x: x.stat().st_mtime,
                    reverse=True,
                )

                if full_backups:
                    last_full_backup = full_backups[0]
                    snapshot_file = local_backup_dir / f".{project_name}_snapshot.json"
                else:
                    # No full backup exists, force full backup
                    incremental = False
                    self.logger.info(f"No full backup found for '{project_name}', performing full backup")

            # Determine backup type and filename
            if incremental and last_full_backup:
                backup_type = "incremental"
                backup_name = f"{project_name}_{timestamp}_incr.tar.gz"
                self.logger.info(f"Starting incremental backup of project '{project_name}'")
            else:
                backup_type = "full"
                backup_name = f"{project_name}_{timestamp}_full.tar.gz"
                self.logger.info(f"Starting full backup of project '{project_name}'")

            # Update local backup path with the correct name
            local_backup_path = local_backup_dir / backup_name

            # Compile exclusion patterns for better performance
            # Add default patterns to always exclude folders starting with _ or .
            default_excludes = [
                "_*/",  # Folders starting with underscore
                ".*/",  # Hidden folders (starting with dot)
            ]
            # Merge defaults with project-specific excludes (avoid duplicates)
            all_exclude_patterns = list(set(default_excludes + exclude_patterns))

            exclude_regexes = self._compile_exclude_patterns(all_exclude_patterns)

            # Load or create snapshot for incremental backup
            file_snapshot = {}
            if incremental and snapshot_file and snapshot_file.exists():
                try:
                    with open(snapshot_file) as f:
                        file_snapshot = json.load(f)
                except (OSError, json.JSONDecodeError):
                    self.logger.warning("Could not load snapshot file, performing full backup")
                    incremental = False
                    backup_type = "full"

            # Create tar archive with exclusions and incremental logic
            with tarfile.open(local_backup_path, "w:gz") as tar:
                new_snapshot = {}
                files_added = 0
                files_skipped = 0

                def filter_func(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    nonlocal files_added, files_skipped

                    # Skip symlinks to prevent traversal outside project directory
                    if tarinfo.issym() or tarinfo.islnk():
                        return None

                    # Check if any path component starts with _ or . (always exclude)
                    # but allow whitelisted dotfiles (e.g. .env, .htaccess)
                    path_parts = tarinfo.name.split("/")
                    for part in path_parts[1:]:  # Skip the root project folder
                        if part.startswith("_") or part.startswith("."):
                            if part not in WHITELISTED_DOTFILES:
                                return None

                    # Check if file should be excluded using compiled regexes
                    for regex in exclude_regexes:
                        if regex.match(tarinfo.name):
                            return None

                    # For incremental backup, check if file has changed
                    if incremental and backup_type == "incremental":
                        file_key = tarinfo.name

                        # Always include directories
                        if tarinfo.isdir():
                            return tarinfo

                        # Check if file is new or modified
                        if file_key in file_snapshot:
                            old_mtime = file_snapshot[file_key].get("mtime", 0)
                            old_size = file_snapshot[file_key].get("size", -1)

                            # Compare modification time and size
                            if tarinfo.mtime <= old_mtime and tarinfo.size == old_size:
                                files_skipped += 1
                                # Still update snapshot for unchanged files
                                new_snapshot[file_key] = {
                                    "mtime": tarinfo.mtime,
                                    "size": tarinfo.size,
                                    "mode": tarinfo.mode,
                                }
                                return None  # Skip unchanged file

                    # Add file to backup and snapshot
                    if tarinfo.isfile():
                        files_added += 1
                        new_snapshot[tarinfo.name] = {
                            "mtime": tarinfo.mtime,
                            "size": tarinfo.size,
                            "mode": tarinfo.mode,
                        }

                    return tarinfo

                tar.add(project_path, arcname=project_name, filter=filter_func)

                if incremental:
                    self.logger.info(f"Incremental backup: {files_added} files added, {files_skipped} files unchanged")

            # Save snapshot for next incremental backup
            if backup_type == "full" or (incremental and new_snapshot):
                snapshot_file = local_backup_dir / f".{project_name}_snapshot.json"
                try:
                    # For full backup, build complete snapshot
                    if backup_type == "full":
                        new_snapshot = {}
                        for root, _dirs, files in os.walk(project_path, followlinks=False):
                            for file in files:
                                file_path = Path(root) / file
                                rel_path = file_path.relative_to(project_path.parent)

                                # Skip excluded files
                                skip = False
                                for regex in exclude_regexes:
                                    if regex.match(str(rel_path)):
                                        skip = True
                                        break

                                if not skip:
                                    try:
                                        stat = file_path.stat()
                                        new_snapshot[str(rel_path)] = {
                                            "mtime": stat.st_mtime,
                                            "size": stat.st_size,
                                            "mode": stat.st_mode,
                                        }
                                    except (OSError, PermissionError) as stat_err:
                                        self.logger.warning(f"Could not stat {file_path}: {stat_err}")

                    from .metadata import _atomic_json_write

                    _atomic_json_write(snapshot_file, new_snapshot)
                    self.logger.debug(f"Saved snapshot with {len(new_snapshot)} files")
                except (OSError, json.JSONDecodeError, TypeError) as e:
                    self.logger.warning(f"Failed to save snapshot: {e}")

            # Finalize: permissions, metadata, sync, symlink, retention
            metadata_extra = {
                "backup_type": backup_type,
                "incremental": incremental,
                "files_added": files_added if incremental else None,
                "files_skipped": files_skipped if incremental else None,
                "base_backup": last_full_backup.name if incremental and last_full_backup else None,
            }
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "project", description,
                f"projects/{project_name}", "latest.tar.gz", retention_days, metadata_extra,
            )
            self.logger.info(f"Successfully backed up '{project_name}' ({size_mb:.2f} MB)")

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "project", size_mb)

            return True, f"Backup successful: {backup_name} ({size_mb:.2f} MB)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to backup project '{project_name}': {error_msg}")

            # Clean up partial archive on failure
            try:
                if "local_backup_path" in locals():
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial archive: {local_backup_path.name}")
            except (FileNotFoundError, NameError):
                pass
            except OSError as cleanup_err:
                self.logger.warning(f"Could not remove partial archive: {cleanup_err}")

            # Send failure notification
            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "project", error_msg)

            return False, f"Backup failed: {error_msg}"

    def backup_project_complete(
        self, project_name: str, description: str | None = None, skip_if_exists_today: bool = False
    ) -> tuple[bool, str]:
        """Create a complete backup of a project including all hidden/config files

        This backup type:
        - Includes .git/, .claude/, _debug/, and all other hidden/underscore folders
        - Only excludes archive files (.zip, .7z, .tar.gz, etc.)
        - Uses a fixed filename (overwrites previous backup)
        - Ideal for preserving all project settings and configs

        Args:
            project_name: Name of the project to backup
            description: Optional description for the backup
            skip_if_exists_today: Skip if backup already exists for today
        """
        self._validate_identifier(project_name, "project name")
        project = self.config.get_project(project_name)

        if not project:
            return False, f"Project '{project_name}' not found in configuration"

        if not project.get("backup", {}).get("enabled", True):
            return False, f"Backup disabled for project '{project_name}'"

        project_path = Path(project["path"])

        if not project_path.exists():
            return False, f"Project path does not exist: {project_path}"

        # Fixed filename for complete backup (no timestamp - overwrites previous)
        backup_name = f"{project_name}_complete.tar.gz"
        local_backup_dir = self.local_path / "projects" / project_name

        # Check if backup already exists today (by checking metadata timestamp)
        if skip_if_exists_today:
            metadata_path = local_backup_dir / f"{project_name}_complete.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        metadata = json.load(f)
                    last_backup = datetime.fromisoformat(metadata.get("timestamp", ""))
                    if last_backup.date() == datetime.now().date():
                        return True, "Skipped: complete backup already exists for today"
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass  # Proceed with backup if metadata is invalid

        # Archive exclusion patterns only (no hidden/underscore folder exclusions)
        archive_patterns = self.config.get_setting(
            "complete_backup.exclude_archives",
            ["*.zip", "*.7z", "*.tar", "*.tar.gz", "*.tgz", "*.tar.bz2", "*.rar", "*.gz", "*.bz2", "*.xz"],
        )

        # Estimate size (include everything except archives)
        estimated_size = self._estimate_project_size_complete(project_path, archive_patterns)
        estimated_compressed_size = int(estimated_size * ESTIMATED_COMPRESSION_RATIO)

        local_backup_dir.mkdir(parents=True, exist_ok=True)
        space_ok, space_msg = self._check_disk_space(local_backup_dir, estimated_compressed_size)

        if not space_ok:
            self.logger.error(f"Disk space check failed for complete backup '{project_name}': {space_msg}")
            return False, space_msg

        try:
            local_backup_path = local_backup_dir / backup_name
            self.logger.info(f"Starting complete backup of project '{project_name}' (including all configs)")

            # Compile archive exclusion patterns
            exclude_regexes = []
            for pattern in archive_patterns:
                regex_pattern = fnmatch.translate(pattern)
                exclude_regexes.append(re.compile(regex_pattern))

            # Create tar archive - only exclude archives, include everything else
            with tarfile.open(local_backup_path, "w:gz") as tar:

                def filter_func(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    # Skip symlinks to prevent traversal outside project directory
                    if tarinfo.issym() or tarinfo.islnk():
                        return None
                    # Only exclude archive files - include all folders including hidden ones
                    filename = os.path.basename(tarinfo.name)
                    for regex in exclude_regexes:
                        if regex.match(filename):
                            return None
                    return tarinfo

                tar.add(project_path, arcname=project_name, filter=filter_func)

            # Finalize: permissions, metadata, sync, symlink, retention
            retention_days = project.get("backup", {}).get(
                "retention_days",
                self.config.get_setting("defaults.project.retention_days", DEFAULT_PROJECT_RETENTION_DAYS),
            )
            size_mb = self._finalize_backup(
                local_backup_path, backup_name, project_name, "project_complete",
                description or "Complete backup (all files including configs)",
                f"projects/{project_name}", "latest_complete.tar.gz", retention_days,
                {"backup_type": "complete", "includes_hidden": True, "includes_git": True},
            )
            self.logger.info(f"Successfully created complete backup of '{project_name}' ({size_mb:.2f} MB)")

            if self.notifier:
                self.notifier.notify_backup_success(project_name, "complete", size_mb)

            return True, f"Complete backup successful: {backup_name} ({size_mb:.2f} MB)"

        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Failed to create complete backup of '{project_name}': {error_msg}")

            # Clean up partial archive
            try:
                if "local_backup_path" in locals():
                    local_backup_path.unlink()
                    self.logger.info(f"Cleaned up partial archive: {local_backup_path.name}")
            except (FileNotFoundError, NameError):
                pass
            except OSError as cleanup_err:
                self.logger.warning(f"Could not remove partial archive: {cleanup_err}")

            if self.notifier:
                self.notifier.notify_backup_failure(project_name, "complete", error_msg)

            return False, f"Complete backup failed: {error_msg}"

    def _estimate_project_size_complete(self, project_path: Path, archive_patterns: list[str]) -> int:
        """Estimate project size for complete backup (includes hidden folders)

        Args:
            project_path: Path to project directory
            archive_patterns: List of archive patterns to exclude

        Returns:
            Estimated size in bytes
        """
        total_size = 0

        exclude_regexes = self._compile_exclude_patterns(archive_patterns)

        try:
            for item in project_path.rglob("*"):
                if item.is_symlink():
                    continue
                if item.is_file():
                    # Only check if it's an archive file
                    filename = item.name
                    is_archive = any(regex.match(filename) for regex in exclude_regexes)

                    if not is_archive:
                        try:
                            total_size += item.stat().st_size
                        except (PermissionError, OSError):
                            pass

        except Exception as e:
            self.logger.warning(f"Error estimating complete backup size: {e}")

        return total_size

    def backup_all_projects_complete(
        self, parallel: bool = True, skip_if_exists_today: bool = False
    ) -> dict[str, tuple[bool, str]]:
        """Create complete backups for all enabled projects

        Args:
            parallel: If True, run backups in parallel
            skip_if_exists_today: Skip projects that already have a complete backup today
        """
        results = {}
        project_names = list(self.config.get_all_projects())

        if not parallel or len(project_names) <= 1:
            for project_name in project_names:
                results[project_name] = self.backup_project_complete(
                    project_name, skip_if_exists_today=skip_if_exists_today
                )
        else:
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel complete backup of {len(project_names)} projects")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_project = {
                    executor.submit(
                        self.backup_project_complete, project_name, None, skip_if_exists_today
                    ): project_name
                    for project_name in project_names
                }

                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Complete backup failed for '{project_name}': {e}")
                        results[project_name] = (False, f"Complete backup failed: {e!s}")

        return results

    def backup_all_projects(
        self, parallel: bool = True, skip_if_exists_today: bool = False, incremental: bool = False
    ) -> dict[str, tuple[bool, str]]:
        """Backup all enabled projects (optionally in parallel)

        Args:
            parallel: If True, run backups in parallel using ThreadPoolExecutor
            skip_if_exists_today: Skip projects that already have a backup today
            incremental: Whether to create incremental backups
        """
        results = {}
        project_names = list(self.config.get_all_projects())

        if not parallel or len(project_names) <= 1:
            # Sequential execution
            for project_name in project_names:
                results[project_name] = self.backup_project(
                    project_name, incremental=incremental, skip_if_exists_today=skip_if_exists_today
                )
        else:
            # Parallel execution
            max_workers = self.config.get_setting("system.max_parallel_backups", 4)
            self.logger.info(f"Starting parallel backup of {len(project_names)} projects with {max_workers} workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all backup tasks
                future_to_project = {
                    executor.submit(
                        self.backup_project, project_name, None, incremental, skip_if_exists_today
                    ): project_name
                    for project_name in project_names
                }

                # Collect results as they complete
                for future in as_completed(future_to_project):
                    project_name = future_to_project[future]
                    try:
                        results[project_name] = future.result()
                    except Exception as e:
                        self.logger.error(f"Parallel backup failed for '{project_name}': {e}", exc_info=True)
                        results[project_name] = (False, f"Backup failed: {e!s}")

        return results

    def restore_project(self, project_name: str, backup_file: str, target_path: str | None = None) -> tuple[bool, str]:
        """Restore a project from backup"""
        self._validate_identifier(project_name, "project name")
        self._validate_backup_filename(backup_file)
        backup_path = self.local_path / "projects" / project_name / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        project = self.config.get_project(project_name)
        if not project and not target_path:
            return False, f"Project '{project_name}' not found in configuration and no target path provided"

        if target_path:
            restore_path = Path(target_path)
            self._validate_restore_target(restore_path)
        else:
            assert project is not None
            restore_path = Path(project["path"])

        try:
            # Verify backup integrity before restoring
            verify_ok, verify_msg = self.verify_backup("project", project_name, backup_file)
            if not verify_ok:
                self.logger.warning(f"Backup verification failed: {verify_msg}")
                return False, f"Restore aborted - backup verification failed: {verify_msg}"

            # Create restore directory if it doesn't exist
            restore_path.parent.mkdir(parents=True, exist_ok=True)

            # If directory exists, rename it as backup
            if restore_path.exists():
                backup_existing = (
                    restore_path.parent / f"{restore_path.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                shutil.move(str(restore_path), str(backup_existing))
                self.logger.info(f"Existing directory moved to: {backup_existing}")

            # Extract backup (with path traversal protection)
            with tarfile.open(backup_path, "r:gz") as tar:
                self._safe_extractall(tar, str(restore_path.parent))

            self.logger.info(f"Successfully restored '{project_name}' from {backup_file}")
            return True, f"Project restored successfully to {restore_path}"

        except Exception as e:
            self.logger.error(f"Failed to restore project '{project_name}': {e!s}")
            return False, f"Restore failed: {e!s}"

    def quick_snapshot(
        self, project_name: str, message: str | None = None, backup_databases: bool = True
    ) -> dict[str, Any]:
        """Create a complete snapshot: Git commit + Project backup + Database backups (if configured)

        This is a convenient command for daily workflow that:
        1. Creates a Git savepoint (if project is a Git repo)
        2. Backs up the project
        3. Backs up associated databases (if any configured for this project)

        Args:
            project_name: Name of the project to snapshot
            message: Commit/backup message
            backup_databases: Whether to backup associated databases (default: True)

        Returns:
            Dictionary with results for each operation
        """
        results: dict[str, Any] = {}

        # Get project configuration
        project = self.config.get_project(project_name)
        if not project:
            return {"error": (False, f"Project '{project_name}' not found in configuration")}

        project_path = Path(project["path"])

        # Step 1: Git savepoint (if it's a Git repo)
        if self.git_manager.is_git_repo(str(project_path)):
            git_msg = message or f"Snapshot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            git_success, git_result = self.git_manager.create_savepoint(str(project_path), git_msg)
            results["git_savepoint"] = (git_success, git_result)
            self.logger.info(f"Git savepoint: {git_result}")
        else:
            results["git_savepoint"] = (False, "Not a Git repository (skipped)")
            self.logger.info(f"Project '{project_name}' is not a Git repository, skipping Git savepoint")

        # Step 2: Project backup
        backup_description = message or f"Quick snapshot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        backup_success, backup_result = self.backup_project(project_name, backup_description)
        results["project_backup"] = (backup_success, backup_result)
        self.logger.info(f"Project backup: {backup_result}")

        # Step 3: Database backups (if configured and requested)
        if backup_databases:
            # Check if project has associated databases configured
            associated_dbs = project.get("databases", [])

            if associated_dbs:
                db_results = {}
                for db_name in associated_dbs:
                    db_success, db_result = self.backup_database(db_name, backup_description)
                    db_results[db_name] = (db_success, db_result)
                    self.logger.info(f"Database '{db_name}' backup: {db_result}")

                results["database_backups"] = db_results
            else:
                results["database_backups"] = (False, "No databases configured for this project")
                self.logger.info(f"No databases configured for project '{project_name}'")

        # Create summary
        success_count = sum(1 for k, v in results.items() if k != "database_backups" and isinstance(v, tuple) and v[0])
        if "database_backups" in results and isinstance(results["database_backups"], dict):
            success_count += sum(1 for v in results["database_backups"].values() if v[0])

        total_operations = len([k for k in results if k != "database_backups" and isinstance(results[k], tuple)])
        if "database_backups" in results and isinstance(results["database_backups"], dict):
            total_operations += len(results["database_backups"])

        results["summary"] = (True, f"Snapshot complete: {success_count}/{total_operations} operations successful")

        # Send snapshot notification
        if self.notifier:
            self.notifier.notify_snapshot_complete(project_name, success_count, total_operations)

        return results

    def list_backup_contents(
        self, item_type: str, item_name: str, backup_file: str, pattern: str | None = None
    ) -> list[dict[str, Any]]:
        """List contents of a backup archive

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project or database
            backup_file: Backup filename to list
            pattern: Optional pattern to filter files (e.g., '*.py', 'src/*')

        Returns:
            List of file information dictionaries
        """
        # Get backup path
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            # Database backups are single SQL files, not much to list
            return []
        else:
            return []

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            self.logger.error(f"Backup file not found: {backup_file}")
            return []

        files = []
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                for member in tar.getmembers():
                    # Apply pattern filter if provided
                    if pattern and not fnmatch.fnmatch(member.name, pattern):
                        continue

                    files.append(
                        {
                            "name": member.name,
                            "type": "dir" if member.isdir() else "file",
                            "size": member.size,
                            "mode": oct(member.mode),
                            "mtime": datetime.fromtimestamp(member.mtime).isoformat(),
                            "uid": member.uid,
                            "gid": member.gid,
                        }
                    )

        except Exception as e:
            self.logger.error(f"Failed to list backup contents: {e}")

        return files

    def selective_restore(
        self,
        item_type: str,
        item_name: str,
        backup_file: str,
        files_to_restore: list[str],
        target_path: str | None = None,
        preserve_structure: bool = True,
    ) -> tuple[bool, str]:
        """Restore specific files from a backup

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project
            backup_file: Backup filename to restore from
            files_to_restore: List of file paths to restore
            target_path: Target directory (optional, defaults to original location)
            preserve_structure: Keep directory structure (True) or flatten (False)

        Returns:
            Tuple of (success, message)
        """
        if item_type != "project":
            return False, "Selective restore only supported for projects"

        self._validate_backup_filename(backup_file)

        # Get backup path
        backup_dir = self.local_path / "projects" / item_name
        backup_path = backup_dir / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Determine target directory
        if target_path:
            restore_dir = Path(target_path)
            self._validate_restore_target(restore_dir)
        else:
            project = self.config.get_project(item_name)
            if not project:
                return False, f"Project '{item_name}' not found and no target path specified"
            restore_dir = Path(project["path"])

        # Check if this is an incremental backup
        is_incremental = "_incr.tar.gz" in backup_file
        restored_files = []

        try:
            # If incremental, we need to check the full backup chain
            files_found = set()
            backups_to_check = [backup_path]

            if is_incremental:
                # Load metadata to find base backup
                metadata_name = backup_file.replace(".tar.gz", ".json")
                metadata_path = backup_dir / metadata_name

                if metadata_path.exists():
                    with open(metadata_path) as f:
                        metadata = json.load(f)

                    base_backup = metadata.get("base_backup")
                    if base_backup:
                        base_path = backup_dir / base_backup
                        if base_path.exists():
                            # Check incremental first, then base
                            backups_to_check = [backup_path, base_path]

            # Process backups (incremental first if applicable)
            for backup in backups_to_check:
                with tarfile.open(backup, "r:gz") as tar:
                    for file_pattern in files_to_restore:
                        # Find matching members
                        for member in tar.getmembers():
                            if fnmatch.fnmatch(member.name, file_pattern):
                                # Skip if already restored from incremental
                                if member.name in files_found:
                                    continue

                                files_found.add(member.name)

                                # Determine extraction path
                                if preserve_structure:
                                    extract_path = restore_dir
                                else:
                                    # Flatten structure - extract to target dir directly
                                    member.name = Path(member.name).name
                                    extract_path = restore_dir

                                # Validate extraction path (prevent path traversal)
                                resolved = (Path(extract_path) / member.name).resolve()
                                if not str(resolved).startswith(str(Path(extract_path).resolve()) + os.sep):
                                    self.logger.warning(f"Skipping unsafe tar member: {member.name}")
                                    continue
                                # Skip symlinks and hardlinks
                                if member.issym() or member.islnk():
                                    self.logger.warning(f"Skipping symlink/hardlink: {member.name}")
                                    continue
                                if sys.version_info >= (3, 12):
                                    tar.extract(member, extract_path, filter="data")
                                else:
                                    tar.extract(member, extract_path)
                                restored_files.append(member.name)
                                self.logger.info(f"Restored: {member.name}")

            if not restored_files:
                return False, f"No files matching patterns: {files_to_restore}"

            self.logger.info(f"Selectively restored {len(restored_files)} files from {backup_file}")
            return True, f"Restored {len(restored_files)} files successfully"

        except Exception as e:
            self.logger.error(f"Failed to restore files: {e}", exc_info=True)
            return False, f"Restore failed: {e!s}"

    def preview_file(
        self, item_type: str, item_name: str, backup_file: str, file_path: str, max_lines: int = 100
    ) -> tuple[bool, str]:
        """Preview a specific file from backup without extracting

        Args:
            item_type: 'project' only
            item_name: Name of the project
            backup_file: Backup filename
            file_path: Path of file to preview within backup
            max_lines: Maximum lines to return (default 100)

        Returns:
            Tuple of (success, content or error message)
        """
        if item_type != "project":
            return False, "Preview only supported for project backups"

        # Get backup path
        backup_dir = self.local_path / "projects" / item_name
        backup_path = backup_dir / backup_file

        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                # Find the file
                member = None
                for m in tar.getmembers():
                    if m.name == file_path:
                        member = m
                        break

                if not member:
                    return False, f"File not found in backup: {file_path}"

                if member.isdir():
                    return False, f"Cannot preview directory: {file_path}"

                # Extract and read file content
                file_obj = tar.extractfile(member)
                if not file_obj:
                    return False, f"Could not extract file: {file_path}"

                # Try to decode as text
                try:
                    with file_obj:
                        content = file_obj.read().decode("utf-8")
                    lines = content.split("\n")

                    if len(lines) > max_lines:
                        preview = "\n".join(lines[:max_lines])
                        preview += f"\n\n... ({len(lines) - max_lines} more lines) ..."
                    else:
                        preview = content

                    return True, preview

                except UnicodeDecodeError:
                    return False, f"File appears to be binary: {file_path}"

        except Exception as e:
            self.logger.error(f"Failed to preview file: {e}")
            return False, f"Preview failed: {e!s}"
