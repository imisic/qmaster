"""
Claude configuration management package.

Assembles ClaudeConfigManager from focused mixins and re-exports
BackupCleanupManager so all existing import patterns keep working.
"""

from utils.claude.advanced_cleanup import _AdvancedCleanupMixin
from utils.claude.base import _ClaudeConfigBase
from utils.claude.cleanup import _CleanupMixin
from utils.claude.conversations import _ConversationsMixin
from utils.claude.mcp import _MCPMixin
from utils.claude.session_inspector import _SessionInspectorMixin
from utils.claude.stats import _StatsMixin
from utils.claude.backup_cleanup import BackupCleanupManager


class ClaudeConfigManager(
    _StatsMixin,
    _MCPMixin,
    _ConversationsMixin,
    _CleanupMixin,
    _AdvancedCleanupMixin,
    _SessionInspectorMixin,
    _ClaudeConfigBase,
):
    """Manages Claude Code configuration and cleanup operations"""


__all__ = ["ClaudeConfigManager", "BackupCleanupManager"]
