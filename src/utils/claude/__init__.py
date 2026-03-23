"""
Claude configuration management package.

Assembles ClaudeConfigManager from focused mixins and re-exports
BackupCleanupManager so all existing import patterns keep working.
"""

from utils.claude.base import _ClaudeConfigBase
from utils.claude.cleanup import _CleanupMixin
from utils.claude.conversations import _ConversationsMixin
from utils.claude.mcp import _MCPMixin
from utils.claude.stats import _StatsMixin
from utils.claude.backup_cleanup import BackupCleanupManager


class ClaudeConfigManager(
    _StatsMixin,
    _MCPMixin,
    _ConversationsMixin,
    _CleanupMixin,
    _ClaudeConfigBase,
):
    """Manages Claude Code configuration and cleanup operations"""


__all__ = ["ClaudeConfigManager", "BackupCleanupManager"]
