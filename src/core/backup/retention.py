"""Retention and cleanup operations for backups."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class RetentionMixin:
    """Mixin providing backup retention and cleanup methods.

    Expects the following attributes on the composing class:
        config: ConfigManager instance
        logger: logging.Logger instance
        local_path: Path to local backup storage
        sync_path: Path | None to secondary storage
    """

    config: Any
    logger: logging.Logger
    local_path: Path
    sync_path: Path | None

    def _cleanup_old_backups(self, directory: Path, retention_days: int, patterns: list[str] | None = None):
        """Remove backups older than retention period (respecting tags and importance).

        Args:
            directory: Directory containing the backup files
            retention_days: Number of days to retain backups
            patterns: Glob patterns to match backup files (default: tar.gz and sql.gz)
        """
        if not directory.exists():
            return

        cutoff_date = datetime.now() - timedelta(days=retention_days)

        if patterns is None:
            patterns = ["*.tar.gz", "*.sql.gz"]

        for pattern in patterns:
            for backup_file in directory.glob(pattern):
                if backup_file.is_symlink():
                    continue  # Skip symlinks

                # Check if backup should be preserved based on metadata
                metadata_name = self._backup_name_to_meta_name(backup_file.name)
                metadata_path = directory / metadata_name

                should_preserve = False
                preserve_reason = None

                if metadata_path.exists():
                    try:
                        with open(metadata_path) as f:
                            metadata = json.load(f)

                        # Check preservation criteria
                        if metadata.get("keep_forever", False) or metadata.get("pinned", False):
                            should_preserve = True
                            preserve_reason = "pinned/keep_forever"
                        elif metadata.get("importance") in ["critical", "high"]:
                            should_preserve = True
                            preserve_reason = f"importance={metadata.get('importance')}"
                        elif metadata.get("tags"):
                            # Preserve if has important tags
                            configured_tags = self.config.get_setting(
                                "retention.important_tags", ["production", "release", "stable", "live", "deployed"]
                            )
                            important_tags = set(configured_tags)
                            if any(tag in important_tags for tag in metadata.get("tags", [])):
                                should_preserve = True
                                preserve_reason = f"tags={metadata.get('tags')}"
                    except Exception as e:
                        self.logger.warning(f"Could not read metadata for {backup_file.name}: {e}")

                # Skip if backup should be preserved
                if should_preserve:
                    self.logger.debug(f"Preserving {backup_file.name} ({preserve_reason})")
                    continue

                # Remove if older than retention period
                if backup_file.stat().st_mtime < cutoff_date.timestamp():
                    backup_file.unlink()

                    # Also remove metadata file
                    if metadata_path.exists():
                        metadata_path.unlink()

                    self.logger.info(f"Removed old backup: {backup_file.name}")
