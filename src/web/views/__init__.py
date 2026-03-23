"""View modules for the Quartermaster dashboard."""

from web.views.dashboard import render_dashboard
from web.views.databases import render_databases
from web.views.logs_diagnostics import render_logs_diagnostics
from web.views.projects import render_projects
from web.views.html_cleaner import render_html_cleaner
from web.views.web_scraper import render_web_scraper
from web.views.storage_cleanup import render_storage_cleanup
from web.views.text_sanitizer import render_text_sanitizer

PAGE_MAP = {
    "Dashboard": render_dashboard,
    "Projects": render_projects,
    "Databases": render_databases,
    "Storage & Cleanup": render_storage_cleanup,
    "Logs & Diagnostics": render_logs_diagnostics,
    "HTML Cleaner": render_html_cleaner,
    "Web Scraper": render_web_scraper,
    "Text Sanitizer": render_text_sanitizer,
}

__all__ = ["PAGE_MAP"]
