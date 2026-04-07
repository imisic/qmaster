"""Backward-compatibility shim — imports from the new core.backup package.

All existing imports like ``from core.backup_engine import BackupEngine``
continue to work unchanged.
"""

from .backup import BackupEngine, DEFAULT_PROJECT_RETENTION_DAYS

__all__ = ["BackupEngine", "DEFAULT_PROJECT_RETENTION_DAYS"]
