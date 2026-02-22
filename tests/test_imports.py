"""Basic smoke tests to verify imports work correctly."""


def test_core_imports():
    """Test that core modules can be imported."""
    from src.core.backup_engine import BackupEngine
    from src.core.config_manager import ConfigManager
    from src.core.git_manager import GitManager

    assert BackupEngine is not None
    assert ConfigManager is not None
    assert GitManager is not None


def test_utils_imports():
    """Test that utility modules can be imported."""
    from src.utils.retention_manager import RetentionManager
    from src.utils.scheduler import BackupScheduler
    from src.utils.storage_analyzer import StorageAnalyzer

    assert RetentionManager is not None
    assert BackupScheduler is not None
    assert StorageAnalyzer is not None
