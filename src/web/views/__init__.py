"""View modules for the Quartermaster dashboard."""

from web.views.claude_code import render_claude_code
from web.views.dashboard import render_dashboard
from web.views.databases import render_databases
from web.views.logs_diagnostics import render_logs_diagnostics
from web.views.projects import render_projects
from web.views.storage_cleanup import render_storage_cleanup
from web.views.tools import render_tools

PAGE_MAP = {
    "Dashboard": render_dashboard,
    "Claude Cleanup": render_claude_code,
    "Logs": render_logs_diagnostics,
    "Projects": render_projects,
    "Databases": render_databases,
    "Storage & Retention": render_storage_cleanup,
    "Tools": render_tools,
}

__all__ = ["PAGE_MAP"]
