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


def test_web_imports():
    """Test that web view modules can be imported and PAGE_MAP is well-formed."""
    from src.web.views import PAGE_MAP

    assert isinstance(PAGE_MAP, dict)
    assert len(PAGE_MAP) == 7, f"Expected 7 sidebar entries, got {len(PAGE_MAP)}"
    for label, render_fn in PAGE_MAP.items():
        assert isinstance(label, str), f"PAGE_MAP key {label!r} is not a string"
        assert callable(render_fn), f"PAGE_MAP value for {label!r} is not callable"
