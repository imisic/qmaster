"""Session state initialization and shared app components."""

from dataclasses import dataclass

import streamlit as st

from core.backup_engine import BackupEngine
from core.config_manager import ConfigManager
from core.git_manager import GitManager
from utils.background_backup import BackgroundBackupManager
from utils.claude_config_manager import BackupCleanupManager, ClaudeConfigManager
from utils.log_parser import ApacheLogParser
from utils.retention_manager import RetentionManager
from web.dashboard_visualizations import DashboardVisualizer


@dataclass
class AppComponents:
    """Shared application components passed to every page."""

    config: ConfigManager
    backup_engine: BackupEngine
    git_manager: GitManager
    apache_parser: ApacheLogParser
    claude_config: ClaudeConfigManager
    backup_cleanup: BackupCleanupManager
    visualizer: DashboardVisualizer
    bg_backup: BackgroundBackupManager
    retention: RetentionManager


@st.cache_resource
def _create_components() -> AppComponents:
    """Create all heavy app components once — cached across reruns and sessions."""
    config = ConfigManager()
    backup = BackupEngine(config)
    git = GitManager()
    apache_parser = ApacheLogParser(config=config)
    paths = config.get_storage_paths()
    local_path = paths.get("local")
    claude_export_path = (local_path / "claude_exports") if local_path else None
    claude_config = ClaudeConfigManager(export_base_path=claude_export_path)
    backup_cleanup = BackupCleanupManager(local_path=local_path, sync_path=paths.get("sync"))
    storage_path = local_path
    assert storage_path is not None, "Local storage path must be configured"
    visualizer = DashboardVisualizer(storage_path)
    bg_backup = BackgroundBackupManager(backup, config)
    retention = RetentionManager(storage_path, config=config)

    return AppComponents(
        config=config,
        backup_engine=backup,
        git_manager=git,
        apache_parser=apache_parser,
        claude_config=claude_config,
        backup_cleanup=backup_cleanup,
        visualizer=visualizer,
        bg_backup=bg_backup,
        retention=retention,
    )


def init_app_state() -> AppComponents:
    """Initialize all app components and session state. Returns AppComponents."""
    app = _create_components()

    # Run startup overdue-backup check once per session
    if "startup_check_done" not in st.session_state:
        st.session_state.startup_check_done = True
        st.session_state.startup_tasks = []

        overdue = app.bg_backup.check_overdue_backups()
        if overdue["projects"] or overdue["databases"]:
            task_ids = app.bg_backup.run_overdue_backups()
            st.session_state.startup_tasks = task_ids

    return app
