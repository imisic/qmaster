"""Metadata, checksum, verification, and tagging operations for backups."""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def metadata_filename(backup_name: str) -> str:
    """Derive the metadata JSON filename from a backup filename."""
    for ext in (".tar.gz", ".sql.gz", ".bundle"):
        if backup_name.endswith(ext):
            return backup_name[: -len(ext)] + ".json"
    return Path(backup_name).stem + ".json"


def _atomic_json_write(path: Path, data: dict) -> None:
    """Write JSON atomically: write to temp file, then rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class MetadataMixin:
    """Mixin providing metadata, checksum, verification, and tagging methods.

    Expects the following attributes on the composing class:
        config: ConfigManager instance
        logger: logging.Logger instance
        local_path: Path to local backup storage
    """

    config: Any
    logger: logging.Logger
    local_path: Path

    def _calculate_file_checksum(self, file_path: Path, algorithm: str = "sha256") -> str:
        """Calculate checksum of a file

        Args:
            file_path: Path to file
            algorithm: Hash algorithm (default: sha256)

        Returns:
            Hexadecimal checksum string
        """
        hash_obj = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            # Read in chunks to handle large files efficiently
            for chunk in iter(lambda: f.read(8192), b""):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    def _create_backup_metadata(
        self,
        backup_dir: Path,
        backup_name: str,
        item_name: str,
        item_type: str,
        description: str | None,
        size_bytes: int,
        backup_file_path: Path | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create metadata file for backup with checksum verification"""
        metadata_name = metadata_filename(backup_name)
        metadata_path = backup_dir / metadata_name

        # Calculate checksum - MANDATORY for new backups
        checksum = None
        if backup_file_path and backup_file_path.exists():
            try:
                checksum = self._calculate_file_checksum(backup_file_path)
                self.logger.info("Calculated SHA256 checksum for %s: %s...", backup_name, checksum[:8])
            except Exception as e:
                # This is now a critical error - we MUST have checksums for data integrity
                self.logger.error("CRITICAL: Failed to calculate checksum for %s: %s", backup_name, e)
                raise RuntimeError(f"Failed to calculate backup checksum: {e}") from e
        else:
            # This should never happen with current code but let's be explicit
            if backup_file_path:
                self.logger.error("CRITICAL: Backup file does not exist for checksum calculation: %s", backup_file_path)
                raise FileNotFoundError(f"Cannot calculate checksum - backup file not found: {backup_file_path}")

        metadata: dict[str, Any] = {
            "backup_name": backup_name,
            "item_name": item_name,
            "item_type": item_type,
            "description": description,
            "timestamp": datetime.now().isoformat(),
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "checksum_sha256": checksum,
            "created_by": "Quartermaster",
            "version": "1.0",
            # New tagging fields
            "tags": [],  # e.g., ['production', 'stable', 'pre-release']
            "importance": "normal",  # critical/high/normal/low
            "keep_forever": False,  # If True, never auto-delete
            "pinned": False,  # Alternative to keep_forever
        }

        # Add any extra metadata
        if extra_metadata:
            metadata.update(extra_metadata)

        try:
            _atomic_json_write(metadata_path, metadata)
            self.logger.info("Created metadata file with checksum: %s", metadata_name)
        except Exception as e:
            self.logger.error("Failed to create metadata file: %s", e)
            raise RuntimeError(f"Failed to create backup metadata: {e}") from e

    def verify_backup(self, item_type: str, item_name: str, backup_file: str) -> tuple[bool, str]:
        """Verify backup integrity by comparing checksums

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database
            backup_file: Backup filename to verify

        Returns:
            Tuple of (success, message)
        """
        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return False, f"Invalid item type: {item_type}"

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Load metadata
        metadata_name = self._backup_name_to_meta_name(backup_file)
        metadata_path = backup_dir / metadata_name

        if not metadata_path.exists():
            return False, f"Metadata file not found: {metadata_name}"

        try:
            with open(metadata_path) as f:
                metadata = json.load(f)

            stored_checksum = metadata.get("checksum_sha256")
            if not stored_checksum:
                return False, "No checksum found in metadata (backup created before verification feature)"

            # Calculate current checksum
            self.logger.info("Verifying backup: %s", backup_file)
            current_checksum = self._calculate_file_checksum(backup_path)

            if current_checksum == stored_checksum:
                self.logger.info("Verification successful for %s", backup_file)
                return True, "✓ Backup verified successfully (checksum matches)"
            else:
                self.logger.error("Verification failed for %s: checksum mismatch", backup_file)
                return (
                    False,
                    f"✗ Backup corrupted! Checksum mismatch.\nExpected: {stored_checksum}\nActual: {current_checksum}",
                )

        except Exception as e:
            self.logger.error("Failed to verify backup %s: %s", backup_file, e, exc_info=True)
            return False, f"Verification failed: {e!s}"

    def verify_all_backups(self, item_type: str, item_name: str) -> dict[str, tuple[bool, str]]:
        """Verify all backups for a project, database, or git repo

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database

        Returns:
            Dictionary mapping backup filenames to (success, message) tuples
        """
        results = {}

        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return {"error": (False, f"Invalid item type: {item_type}")}

        if not backup_dir.exists():
            return {"error": (False, f"No backups found for {item_name}")}

        # Find all backup files
        backup_files = []
        if item_type == "project":
            backup_files = list(backup_dir.glob("*.tar.gz"))
        elif item_type == "database":
            backup_files = list(backup_dir.glob("*.sql.gz"))
        elif item_type == "git":
            backup_files = list(backup_dir.glob("*.bundle"))

        backup_files = [b for b in backup_files if not b.is_symlink()]

        if not backup_files:
            return {"error": (False, f"No backups found for {item_name}")}

        self.logger.info("Verifying %s backups for %s...", len(backup_files), item_name)

        for backup_file in backup_files:
            success, message = self.verify_backup(item_type, item_name, backup_file.name)
            results[backup_file.name] = (success, message)

        return results

    def get_backup_status(self, item_type: str, item_name: str) -> dict[str, Any]:
        """Get backup status for a project, database, or git backup"""
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            backup_dir = self.local_path / "databases" / item_name

        if not backup_dir.exists():
            return {"exists": False, "backup_count": 0, "total_size": 0, "latest_backup": None}

        # Different file patterns for different types
        if item_type == "git":
            backups = list(backup_dir.glob("*.bundle"))
        else:
            backups = list(backup_dir.glob("*.tar.gz")) + list(backup_dir.glob("*.sql.gz"))
        backups = [b for b in backups if not b.is_symlink()]

        if not backups:
            return {"exists": True, "backup_count": 0, "total_size": 0, "latest_backup": None}

        backups.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest = backups[0]

        total_size = sum(b.stat().st_size for b in backups)

        return {
            "exists": True,
            "backup_count": len(backups),
            "total_size": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "latest_backup": {
                "name": latest.name,
                "size": latest.stat().st_size,
                "size_mb": latest.stat().st_size / (1024 * 1024),
                "modified": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
            },
            "all_backups": [
                {
                    "name": b.name,
                    "size_mb": b.stat().st_size / (1024 * 1024),
                    "modified": datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
                }
                for b in backups[:10]  # Last 10 backups
            ],
        }

    def tag_backup(
        self,
        item_type: str,
        item_name: str,
        backup_file: str,
        tags: list[str] | None = None,
        importance: str | None = None,
        keep_forever: bool | None = None,
        description: str | None = None,
    ) -> tuple[bool, str]:
        """Tag a backup with metadata for preservation and organization

        Args:
            item_type: 'project', 'database', or 'git'
            item_name: Name of the project or database
            backup_file: Backup filename to tag
            tags: List of tags to add (e.g., ['production', 'stable'])
            importance: Importance level ('critical', 'high', 'normal', 'low')
            keep_forever: If True, backup will never be auto-deleted
            description: Update or add description

        Returns:
            Tuple of (success, message)
        """
        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
        else:
            return False, f"Invalid item type: {item_type}"

        backup_path = backup_dir / backup_file
        if not backup_path.exists():
            return False, f"Backup file not found: {backup_file}"

        # Load or create metadata
        metadata_name = self._backup_name_to_meta_name(backup_file)
        metadata_path = backup_dir / metadata_name

        if metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    metadata = json.load(f)
            except Exception as e:
                return False, f"Failed to read metadata: {e}"
        else:
            # Create basic metadata if it doesn't exist
            file_stats = backup_path.stat()
            metadata = {
                "backup_name": backup_file,
                "item_name": item_name,
                "item_type": item_type,
                "timestamp": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                "size_bytes": file_stats.st_size,
                "size_mb": round(file_stats.st_size / (1024 * 1024), 2),
                "created_by": "Manual Tag Operation",
                "version": "1.0",
            }

        # Update metadata with new values
        if tags is not None:
            existing_tags = set(metadata.get("tags", []))
            existing_tags.update(tags)
            metadata["tags"] = sorted(existing_tags)

        if importance is not None:
            if importance not in ["critical", "high", "normal", "low"]:
                return False, f"Invalid importance level: {importance}"
            metadata["importance"] = importance

        if keep_forever is not None:
            metadata["keep_forever"] = keep_forever
            metadata["pinned"] = keep_forever  # Set both for compatibility

        if description is not None:
            metadata["description"] = description

        # Add tagging metadata
        metadata["last_modified"] = datetime.now().isoformat()
        metadata["last_modified_by"] = "Tag Operation"

        # Save updated metadata
        try:
            _atomic_json_write(metadata_path, metadata)

            tag_summary = []
            if tags:
                tag_summary.append(f"tags={metadata['tags']}")
            if importance:
                tag_summary.append(f"importance={importance}")
            if keep_forever:
                tag_summary.append("pinned")
            if description:
                tag_summary.append("description updated")

            self.logger.info("Tagged %s: %s", backup_file, ', '.join(tag_summary))
            return True, f"Successfully tagged {backup_file}: {', '.join(tag_summary)}"

        except Exception as e:
            self.logger.error("Failed to save metadata for %s: %s", backup_file, e)
            return False, f"Failed to tag backup: {e}"

    def list_tagged_backups(self, item_type: str | None = None, item_name: str | None = None) -> list[dict[str, Any]]:
        """List all tagged backups

        Args:
            item_type: Filter by 'project', 'database', or 'git' (optional)
            item_name: Filter by specific project/database name (optional)

        Returns:
            List of tagged backup metadata
        """
        tagged_backups = []

        # Determine directories to search
        search_dirs = []
        if item_type in ["project", None]:
            projects_dir = self.local_path / "projects"
            if item_name:
                specific_dir = projects_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("project", item_name, specific_dir))
            else:
                for project_dir in projects_dir.glob("*/"):
                    if project_dir.is_dir():
                        search_dirs.append(("project", project_dir.name, project_dir))

        if item_type in ["database", None]:
            databases_dir = self.local_path / "databases"
            if item_name:
                specific_dir = databases_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("database", item_name, specific_dir))
            else:
                for db_dir in databases_dir.glob("*/"):
                    if db_dir.is_dir():
                        search_dirs.append(("database", db_dir.name, db_dir))

        if item_type in ["git", None]:
            git_dir = self.local_path / "git"
            if item_name:
                specific_dir = git_dir / item_name
                if specific_dir.exists():
                    search_dirs.append(("git", item_name, specific_dir))
            else:
                for repo_dir in git_dir.glob("*/"):
                    if repo_dir.is_dir():
                        search_dirs.append(("git", repo_dir.name, repo_dir))

        # Search for tagged backups
        for type_name, name, directory in search_dirs:
            for metadata_file in directory.glob("*.json"):
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)

                    # Check if backup is tagged
                    is_tagged = (
                        metadata.get("tags")
                        or metadata.get("keep_forever", False)
                        or metadata.get("pinned", False)
                        or metadata.get("importance") not in [None, "normal"]
                    )

                    if is_tagged:
                        metadata["item_type"] = type_name
                        metadata["item_name"] = name
                        tagged_backups.append(metadata)

                except Exception as e:
                    self.logger.warning("Could not read metadata file %s: %s", metadata_file, e)

        # Sort by timestamp (newest first)
        tagged_backups.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return tagged_backups

    def backfill_checksums(self, item_type: str, item_name: str) -> tuple[int, int]:
        """Add checksums to old backups that don't have them

        Args:
            item_type: 'project' or 'database'
            item_name: Name of the project or database

        Returns:
            Tuple of (updated_count, total_count)
        """
        updated = 0
        total = 0

        # Get backup directory
        if item_type == "project":
            backup_dir = self.local_path / "projects" / item_name
            pattern = "*.tar.gz"
        elif item_type == "database":
            backup_dir = self.local_path / "databases" / item_name
            pattern = "*.sql.gz"
        elif item_type == "git":
            backup_dir = self.local_path / "git" / item_name
            pattern = "*.bundle"
        else:
            return 0, 0

        if not backup_dir.exists():
            return 0, 0

        # Find all backup files
        backup_files = [f for f in backup_dir.glob(pattern) if not f.is_symlink()]
        total = len(backup_files)

        for backup_file in backup_files:
            # Check if metadata exists
            metadata_name = self._backup_name_to_meta_name(backup_file.name)
            metadata_path = backup_dir / metadata_name

            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        metadata = json.load(f)

                    # Check if checksum is missing or None
                    if not metadata.get("checksum_sha256"):
                        self.logger.info("Calculating checksum for %s...", backup_file.name)

                        # Calculate checksum
                        checksum = self._calculate_file_checksum(backup_file)

                        # Update metadata
                        metadata["checksum_sha256"] = checksum
                        metadata["checksum_added"] = datetime.now().isoformat()
                        metadata["checksum_added_by"] = "Backfill Operation"

                        # Save updated metadata
                        _atomic_json_write(metadata_path, metadata)

                        self.logger.info("Added checksum to %s: %s...", metadata_name, checksum[:8])
                        updated += 1

                except Exception as e:
                    self.logger.error("Failed to update metadata for %s: %s", backup_file.name, e)
            else:
                # Create metadata if it doesn't exist
                try:
                    self.logger.info("Creating metadata for %s...", backup_file.name)

                    # Calculate checksum
                    checksum = self._calculate_file_checksum(backup_file)
                    file_stats = backup_file.stat()

                    # Create new metadata
                    metadata = {
                        "backup_name": backup_file.name,
                        "item_name": item_name,
                        "item_type": item_type,
                        "description": None,
                        "timestamp": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        "size_bytes": file_stats.st_size,
                        "size_mb": round(file_stats.st_size / (1024 * 1024), 2),
                        "checksum_sha256": checksum,
                        "created_by": "Backfill Operation",
                        "version": "1.0",
                    }

                    # Save metadata
                    _atomic_json_write(metadata_path, metadata)

                    self.logger.info("Created metadata for %s with checksum: %s...", backup_file.name, checksum[:8])
                    updated += 1

                except Exception as e:
                    self.logger.error("Failed to create metadata for %s: %s", backup_file.name, e)

        return updated, total
