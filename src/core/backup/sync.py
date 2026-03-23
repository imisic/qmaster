"""Secondary sync operations for backups."""

import logging
import shutil
from pathlib import Path


class SyncMixin:
    """Mixin providing secondary storage sync methods.

    Expects the following attributes on the composing class:
        logger: logging.Logger instance
        local_path: Path to local backup storage
        sync_path: Path | None to secondary storage
    """

    logger: logging.Logger
    local_path: Path
    sync_path: Path | None

    def _sync_to_secondary(self, local_file: Path, subdirectory: str, backup_name: str) -> None:
        """Sync backup file and its metadata JSON to secondary storage.

        Args:
            local_file: Path to the local backup file
            subdirectory: Relative path under sync root (e.g. 'projects/myapp')
            backup_name: Filename of the backup
        """
        if not (self.sync_path and self.sync_path != self.local_path):
            return

        sync_dir = self.sync_path / subdirectory
        sync_dir.mkdir(parents=True, exist_ok=True)

        # Sync backup file
        if not self._smart_copy(local_file, sync_dir / backup_name):
            self.logger.info(f"Skipped secondary sync for {backup_name} (file unchanged)")

        # Sync companion metadata JSON
        metadata_name = backup_name.replace(".tar.gz", ".json").replace(".sql.gz", ".json").replace(".bundle", ".json")
        metadata_path = local_file.parent / metadata_name
        if metadata_path.exists():
            self._smart_copy(metadata_path, sync_dir / metadata_name)

    def _smart_copy(self, source: Path, destination: Path) -> bool:
        """Copy file only if different (checksum-based)

        Args:
            source: Source file path
            destination: Destination file path

        Returns:
            True if file was copied, False if skipped (identical)
        """
        # If destination doesn't exist, copy
        if not destination.exists():
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (new file)")
            return True

        # Quick size check
        source_size = source.stat().st_size
        dest_size = destination.stat().st_size

        if source_size != dest_size:
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (size changed)")
            return True

        # Checksum comparison
        source_checksum = self._calculate_file_checksum(source)
        dest_checksum = self._calculate_file_checksum(destination)

        if source_checksum != dest_checksum:
            shutil.copy2(source, destination)
            self.logger.debug(f"Copied {source.name} to {destination} (checksum mismatch)")
            return True

        self.logger.debug(f"Skipped copying {source.name} (identical)")
        return False
