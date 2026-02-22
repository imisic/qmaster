"""View modules for the Quartermaster dashboard."""

from web.views.dashboard import render_dashboard
from web.views.databases import render_databases
from web.views.logs_diagnostics import render_logs_diagnostics
from web.views.projects import render_projects
from web.views.html_cleaner import render_html_cleaner
from web.views.storage_cleanup import render_storage_cleanup

PAGE_MAP = {
    "Dashboard": render_dashboard,
    "Projects": render_projects,
    "Databases": render_databases,
    "Storage & Cleanup": render_storage_cleanup,
    "Logs & Diagnostics": render_logs_diagnostics,
    "HTML Cleaner": render_html_cleaner,
}

__all__ = ["PAGE_MAP"]
