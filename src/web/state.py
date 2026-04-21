"""Session state initialization and shared app components."""

from dataclasses import dataclass

import streamlit as st

from core.backup_engine import BackupEngine
from core.config_manager import ConfigManager
from core.git_manager import GitManager
from utils.background_backup import BackgroundBackupManager
from utils.claude import BackupCleanupManager, ClaudeConfigManager
from utils.log_parser import ApacheLogParser
from utils.retention_manager import RetentionManager
from web.dashboard_visualizations import DashboardVisualizer


@dataclass
class _CachedComponents:
    """Heavy components cached across reruns."""

    config: ConfigManager
    backup_engine: BackupEngine
    git_manager: GitManager
    apache_parser: ApacheLogParser
    backup_cleanup: BackupCleanupManager
    visualizer: DashboardVisualizer
    bg_backup: BackgroundBackupManager
    retention: RetentionManager


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
def _create_components() -> _CachedComponents:
    """Create heavy app components once — cached across reruns and sessions."""
    config = ConfigManager()
    backup = BackupEngine(config)
    git = GitManager()
    apache_parser = ApacheLogParser(config=config)
    paths = config.get_storage_paths()
    local_path = paths.get("local")
    backup_cleanup = BackupCleanupManager(local_path=local_path, sync_path=paths.get("sync"))
    storage_path = local_path
    if storage_path is None:
        raise RuntimeError("Local storage path must be configured")
    visualizer = DashboardVisualizer(storage_path)
    bg_backup = BackgroundBackupManager(backup, config)
    retention = RetentionManager(storage_path, config=config)

    return _CachedComponents(
        config=config,
        backup_engine=backup,
        git_manager=git,
        apache_parser=apache_parser,
        backup_cleanup=backup_cleanup,
        visualizer=visualizer,
        bg_backup=bg_backup,
        retention=retention,
    )


def init_app_state() -> AppComponents:
    """Initialize all app components and session state. Returns AppComponents."""
    cached = _create_components()

    # Built fresh per rerun so mixin edits hot-reload without restarting Streamlit.
    paths = cached.config.get_storage_paths()
    local_path = paths.get("local")
    claude_export_path = (local_path / "claude_exports") if local_path else None

    # Load configured claude_dirs from settings, or let auto-detection handle it
    from pathlib import Path as _Path

    claude_dirs_setting = cached.config.get_setting("claude_config.claude_dirs")
    claude_dirs = [_Path(p).expanduser() for p in claude_dirs_setting] if claude_dirs_setting else None
    claude_config = ClaudeConfigManager(export_base_path=claude_export_path, claude_dirs=claude_dirs)

    app = AppComponents(
        config=cached.config,
        backup_engine=cached.backup_engine,
        git_manager=cached.git_manager,
        apache_parser=cached.apache_parser,
        claude_config=claude_config,
        backup_cleanup=cached.backup_cleanup,
        visualizer=cached.visualizer,
        bg_backup=cached.bg_backup,
        retention=cached.retention,
    )

    # Run startup overdue-backup check once per session
    if "startup_check_done" not in st.session_state:
        st.session_state.startup_check_done = True
        st.session_state.startup_tasks = []

        overdue = app.bg_backup.check_overdue_backups()
        if overdue["projects"] or overdue["databases"]:
            task_ids = app.bg_backup.run_overdue_backups()
            st.session_state.startup_tasks = task_ids

    return app
