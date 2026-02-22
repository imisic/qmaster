#!/usr/bin/env python3
"""Command Line Interface for Quartermaster"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console
from rich.table import Table

# Add parent directory to path
sys.path.append(str(Path(__file__).parent))

from core.backup_engine import DEFAULT_PROJECT_RETENTION_DAYS, BackupEngine
from core.config_manager import ConfigManager
from core.git_manager import GitManager
from utils.log_parser import ApacheLogParser
from utils.php_log_parser import PHPLogParser
from utils.retention_manager import RetentionManager
from utils.scheduler import BackupScheduler
from utils.storage_analyzer import StorageAnalyzer

console = Console()

# File extension to syntax highlighting language mapping
_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".php": "php",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "bash",
    ".sql": "sql",
}

# Lazy-initialized components (created on first access to avoid startup cost)
_components: dict[str, Any] = {}


def _get_config() -> ConfigManager:
    if "config" not in _components:
        _components["config"] = ConfigManager()
    return cast("ConfigManager", _components["config"])


def _get_backup_engine() -> BackupEngine:
    if "backup_engine" not in _components:
        _components["backup_engine"] = BackupEngine(_get_config())
    return cast("BackupEngine", _components["backup_engine"])


def _get_git_manager() -> GitManager:
    if "git_manager" not in _components:
        _components["git_manager"] = GitManager()
    return cast("GitManager", _components["git_manager"])


def _get_apache_parser() -> ApacheLogParser:
    if "apache_parser" not in _components:
        _components["apache_parser"] = ApacheLogParser(config=_get_config())
    return cast("ApacheLogParser", _components["apache_parser"])


def _get_php_parser() -> PHPLogParser:
    if "php_parser" not in _components:
        project_paths = [proj["path"] for proj in _get_config().get_all_projects().values()]
        _components["php_parser"] = PHPLogParser(project_paths)
    return cast("PHPLogParser", _components["php_parser"])


def _get_scheduler() -> BackupScheduler:
    if "scheduler" not in _components:
        _components["scheduler"] = BackupScheduler()
    return cast("BackupScheduler", _components["scheduler"])


def _get_storage_analyzer() -> StorageAnalyzer:
    if "storage_analyzer" not in _components:
        storage_path = _get_config().get_storage_paths()["local"]
        assert storage_path is not None
        _components["storage_analyzer"] = StorageAnalyzer(storage_path, config=_get_config())
    return cast("StorageAnalyzer", _components["storage_analyzer"])


def _get_retention_manager() -> RetentionManager:
    if "retention_manager" not in _components:
        storage_path = _get_config().get_storage_paths()["local"]
        assert storage_path is not None
        _components["retention_manager"] = RetentionManager(storage_path, config=_get_config())
    return cast("RetentionManager", _components["retention_manager"])


@click.group()
def cli():
    """Quartermaster - CLI Interface"""
    pass


@cli.command()
@click.option("--all", is_flag=True, help="Backup all projects")
@click.option("--project", help="Backup specific project")
@click.option("--description", "-d", help="Description for the backup (reason for creating it)")
@click.option("--incremental", "-i", is_flag=True, help="Perform incremental backup (only changed files)")
@click.option("--full", "-f", is_flag=True, help="Force full backup even if incremental is possible")
def backup(all, project, description, incremental, full):
    """Backup projects"""
    # Handle conflicting flags
    if incremental and full:
        console.print("[red]Error: Cannot use both --incremental and --full flags[/red]")
        return

    # Determine backup mode
    use_incremental = incremental and not full

    if all:
        console.print("[bold cyan]Backing up all projects...[/bold cyan]")
        if incremental:
            console.print("[yellow]Note: Incremental mode will be used where possible[/yellow]")
        results = _get_backup_engine().backup_all_projects(incremental=use_incremental)

        for name, (success, message) in results.items():
            if success:
                console.print(f"[green]✓[/green] {name}: {message}")
            else:
                console.print(f"[red]✗[/red] {name}: {message}")

    elif project:
        backup_mode = "incremental" if use_incremental else ("full" if full else "auto")
        console.print(f"[bold cyan]Backing up project '{project}' ({backup_mode} mode)...[/bold cyan]")
        if description:
            console.print(f"[dim]Reason: {description}[/dim]")
        success, message = _get_backup_engine().backup_project(project, description, incremental=use_incremental)

        if success:
            console.print(f"[green]✓[/green] {message}")
        else:
            console.print(f"[red]✗[/red] {message}")

    else:
        console.print("[yellow]Please specify --all or --project NAME[/yellow]")


@cli.command()
@click.option("--all", is_flag=True, help="Backup all databases")
@click.option("--database", help="Backup specific database")
@click.option("--description", "-d", help="Description for the backup (reason for creating it)")
def backup_db(all, database, description):
    """Backup databases"""
    if all:
        console.print("[bold cyan]Backing up all databases...[/bold cyan]")
        results = _get_backup_engine().backup_all_databases()

        for name, (success, message) in results.items():
            if success:
                console.print(f"[green]✓[/green] {name}: {message}")
            else:
                console.print(f"[red]✗[/red] {name}: {message}")

    elif database:
        console.print(f"[bold cyan]Backing up database '{database}'...[/bold cyan]")
        if description:
            console.print(f"[dim]Reason: {description}[/dim]")
        success, message = _get_backup_engine().backup_database(database, description)

        if success:
            console.print(f"[green]✓[/green] {message}")
        else:
            console.print(f"[red]✗[/red] {message}")

    else:
        console.print("[yellow]Please specify --all or --database NAME[/yellow]")


@cli.command("backup-git")
@click.option("--all", is_flag=True, help="Backup git history for all projects")
@click.option("--project", help="Backup git history for specific project")
@click.option("--description", "-d", help="Description for the backup")
def backup_git(all, project, description):
    """Backup git history as portable bundle files

    Git bundles are single-file archives containing full git history.
    They can be restored to any location and preserve all branches and commits.
    """
    if all:
        console.print("[bold cyan]Backing up git history for all projects...[/bold cyan]")
        console.print("[dim]Only projects with git repositories will be backed up[/dim]\n")

        results = _get_backup_engine().backup_all_git()

        if "error" in results:
            console.print(f"[red]✗ {results['error'][1]}[/red]")
            return

        success_count = sum(1 for s, _ in results.values() if s)
        for name, (success, message) in results.items():
            if success:
                console.print(f"[green]✓[/green] {name}: {message}")
            else:
                console.print(f"[red]✗[/red] {name}: {message}")

        console.print(f"\n[bold]Complete: {success_count}/{len(results)} git backups created[/bold]")

    elif project:
        console.print(f"[bold cyan]Backing up git history for '{project}'...[/bold cyan]")
        if description:
            console.print(f"[dim]Description: {description}[/dim]")

        success, message = _get_backup_engine().backup_git(project, description)

        if success:
            console.print(f"[green]✓[/green] {message}")
        else:
            console.print(f"[red]✗[/red] {message}")

    else:
        console.print("[yellow]Please specify --all or --project NAME[/yellow]")


@cli.command("restore-git")
@click.argument("project")
@click.argument("backup_file")
@click.option("--target", help="Target restore path (optional)")
@click.option(
    "--mode",
    type=click.Choice(["clone", "fetch"]),
    default="clone",
    help="clone: create new repo, fetch: update existing repo",
)
def restore_git(project, backup_file, target, mode):
    """Restore a git repository from a bundle backup"""
    console.print(f"[bold cyan]Restoring git backup for '{project}'...[/bold cyan]")
    console.print(f"[dim]Mode: {mode}[/dim]")

    success, message = _get_backup_engine().restore_git(project, backup_file, target, mode)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command("backup-complete")
@click.option("--all", is_flag=True, help="Complete backup of all projects")
@click.option("--project", help="Complete backup of specific project")
@click.option("--description", "-d", help="Description for the backup")
def backup_complete(all, project, description):
    """Create complete backups (includes .git, .claude, _debug, etc.)

    Complete backups include ALL files in the project folder except archive files
    (.zip, .7z, .tar.gz, etc.). This preserves git history, claude configs, and
    all other hidden/config files that are normally excluded from regular backups.

    Uses a fixed filename that overwrites the previous backup.
    """
    if all:
        console.print("[bold cyan]Creating complete backups of all projects...[/bold cyan]")
        console.print("[dim]Including: .git/, .claude/, _debug/, and all hidden folders[/dim]")
        console.print("[dim]Excluding: only archive files (.zip, .7z, .tar.gz, etc.)[/dim]\n")

        results = _get_backup_engine().backup_all_projects_complete()

        success_count = sum(1 for s, _ in results.values() if s)
        for name, (success, message) in results.items():
            if success:
                console.print(f"[green]✓[/green] {name}: {message}")
            else:
                console.print(f"[red]✗[/red] {name}: {message}")

        console.print(f"\n[bold]Complete: {success_count}/{len(results)} projects backed up[/bold]")

    elif project:
        console.print(f"[bold cyan]Creating complete backup of '{project}'...[/bold cyan]")
        console.print("[dim]Including: .git/, .claude/, _debug/, and all hidden folders[/dim]")
        if description:
            console.print(f"[dim]Reason: {description}[/dim]")

        success, message = _get_backup_engine().backup_project_complete(project, description)

        if success:
            console.print(f"[green]✓[/green] {message}")
        else:
            console.print(f"[red]✗[/red] {message}")

    else:
        console.print("[yellow]Please specify --all or --project NAME[/yellow]")


@cli.command()
def status():
    """Show backup status"""
    console.print("[bold cyan]Quartermaster - Status[/bold cyan]\n")

    # Projects table
    projects_table = Table(title="Projects", show_header=True, header_style="bold magenta")
    projects_table.add_column("Name", style="cyan", width=20)
    projects_table.add_column("Path", style="white")
    projects_table.add_column("Backups", justify="right")
    projects_table.add_column("Size", justify="right")
    projects_table.add_column("Latest", style="green")

    for name, project in _get_config().get_all_projects().items():
        status = _get_backup_engine().get_backup_status("project", name)

        if status["exists"]:
            latest = status["latest_backup"]["name"][:30] if status["latest_backup"] else "None"
            size = f"{status.get('total_size_mb', 0):.1f} MB"
        else:
            latest = "No backups"
            size = "0 MB"

        projects_table.add_row(
            name,
            project["path"][:40] + "..." if len(project["path"]) > 40 else project["path"],
            str(status.get("backup_count", 0)),
            size,
            latest,
        )

    console.print(projects_table)
    console.print()

    # Git backups table
    git_table = Table(title="Git Backups", show_header=True, header_style="bold magenta")
    git_table.add_column("Project", style="cyan", width=20)
    git_table.add_column("Backups", justify="right")
    git_table.add_column("Size", justify="right")
    git_table.add_column("Latest", style="green")

    has_git_backups = False
    for name, project in _get_config().get_all_projects().items():
        if _get_git_manager().is_git_repo(project["path"]):
            status = _get_backup_engine().get_backup_status("git", name)

            if status["exists"] and status.get("backup_count", 0) > 0:
                has_git_backups = True
                latest = status["latest_backup"]["name"][:35] if status["latest_backup"] else "None"
                size = f"{status.get('total_size_mb', 0):.1f} MB"

                git_table.add_row(name, str(status.get("backup_count", 0)), size, latest)

    if has_git_backups:
        console.print(git_table)
        console.print()
    else:
        console.print("[dim]No git backups yet. Use 'backup-git --project NAME' to create one.[/dim]")
        console.print()

    # Databases table
    db_table = Table(title="Databases", show_header=True, header_style="bold magenta")
    db_table.add_column("Name", style="cyan", width=20)
    db_table.add_column("Type", style="white")
    db_table.add_column("Host", style="white")
    db_table.add_column("Backups", justify="right")
    db_table.add_column("Size", justify="right")
    db_table.add_column("Latest", style="green")

    for name, db in _get_config().get_all_databases().items():
        status = _get_backup_engine().get_backup_status("database", name)

        if status["exists"]:
            latest = status["latest_backup"]["name"][:30] if status["latest_backup"] else "None"
            size = f"{status.get('total_size_mb', 0):.1f} MB"
        else:
            latest = "No backups"
            size = "0 MB"

        db_table.add_row(
            name, db.get("type", "mysql"), db.get("host", "localhost"), str(status.get("backup_count", 0)), size, latest
        )

    console.print(db_table)
    console.print()

    # Storage info
    storage_paths = _get_config().get_storage_paths()
    console.print("[bold]Storage Locations:[/bold]")
    console.print(f"  Local: {storage_paths.get('local', 'Not configured')}")
    if storage_paths.get("sync"):
        console.print(f"  Sync: {storage_paths['sync']}")


@cli.command()
@click.argument("project")
@click.option("--message", "-m", help="Commit message")
def savepoint(project, message):
    """Create Git savepoint for a project"""
    project_config = _get_config().get_project(project)

    if not project_config:
        console.print(f"[red]Project '{project}' not found[/red]")
        return

    if not message:
        message = f"Savepoint - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    console.print(f"[bold cyan]Creating savepoint for '{project}'...[/bold cyan]")
    success, result_message = _get_git_manager().create_savepoint(project_config["path"], message)

    if success:
        console.print(f"[green]✓[/green] {result_message}")
    else:
        console.print(f"[red]✗[/red] {result_message}")


@cli.command()
@click.argument("project")
@click.argument("backup_file")
@click.option("--target", help="Target restore path (optional)")
def restore(project, backup_file, target):
    """Restore a project from backup"""
    console.print(f"[bold cyan]Restoring '{project}' from '{backup_file}'...[/bold cyan]")

    success, message = _get_backup_engine().restore_project(project, backup_file, target)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.argument("database")
@click.argument("backup_file")
def restore_db(database, backup_file):
    """Restore a database from backup"""
    console.print(f"[bold cyan]Restoring database '{database}' from '{backup_file}'...[/bold cyan]")

    success, message = _get_backup_engine().restore_database(database, backup_file)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.argument("project")
@click.argument("backup_file")
@click.option("--pattern", help='Filter pattern (e.g., "*.py", "src/*")')
def list_files(project, backup_file, pattern):
    """List files in a backup archive"""
    console.print(f"[bold cyan]Listing contents of {backup_file}...[/bold cyan]")

    files = _get_backup_engine().list_backup_contents("project", project, backup_file, pattern)

    if not files:
        console.print("[yellow]No files found or backup doesn't exist[/yellow]")
        return

    table = Table(title=f"Files in {backup_file}", show_header=True, header_style="bold magenta")
    table.add_column("Type", style="cyan", width=6)
    table.add_column("Name", style="white")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="green")

    for file in files:
        file_type = "📁" if file["type"] == "dir" else "📄"
        size_str = f"{file['size']:,}" if file["type"] == "file" else "-"
        table.add_row(
            file_type,
            file["name"],
            size_str,
            file["mtime"][:19],  # Show date and time only
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(files)} items[/dim]")


@cli.command()
@click.argument("project")
@click.argument("backup_file")
@click.argument("files", nargs=-1, required=True)
@click.option("--target", help="Target directory for restore (defaults to original location)")
@click.option("--flatten", is_flag=True, help="Extract files without directory structure")
def restore_files(project, backup_file, files, target, flatten):
    """Restore specific files from a backup

    Examples:
        restore-files myproject backup.tar.gz "src/*.py" "config/*"
        restore-files myproject backup.tar.gz README.md --target /tmp/restore
    """
    console.print(f"[bold cyan]Restoring {len(files)} file pattern(s) from {backup_file}...[/bold cyan]")

    success, message = _get_backup_engine().selective_restore(
        "project", project, backup_file, list(files), target, preserve_structure=not flatten
    )

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.argument("project")
@click.argument("backup_file")
@click.argument("file_path")
@click.option("--lines", default=100, help="Maximum lines to preview (default: 100)")
def preview_file(project, backup_file, file_path, lines):
    """Preview a file from backup without extracting"""
    console.print(f"[bold cyan]Preview of {file_path} from {backup_file}:[/bold cyan]\n")

    success, content = _get_backup_engine().preview_file("project", project, backup_file, file_path, lines)

    if success:
        # Use syntax highlighting if possible
        from rich.syntax import Syntax

        # Detect language from file extension
        ext = Path(file_path).suffix
        language = _LANGUAGE_MAP.get(ext, "text")

        try:
            syntax = Syntax(content, language, theme="monokai", line_numbers=True)
            console.print(syntax)
        except Exception:
            # Fallback to plain text if syntax highlighting fails
            console.print(content)
    else:
        console.print(f"[red]Error: {content}[/red]")


@cli.command()
def list_projects():
    """List all configured projects"""
    projects = _get_config().get_all_projects()

    if not projects:
        console.print("[yellow]No projects configured[/yellow]")
        return

    table = Table(title="Configured Projects", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="white")
    table.add_column("Path", style="white")
    table.add_column("Git", style="green")
    table.add_column("Schedule", style="yellow")

    for name, project in projects.items():
        table.add_row(
            name,
            project.get("type", "unknown"),
            project["path"],
            "✓" if project.get("git", {}).get("track", False) else "✗",
            project.get("backup", {}).get("schedule", "manual"),
        )

    console.print(table)


@cli.command()
def list_databases():
    """List all configured databases"""
    databases = _get_config().get_all_databases()

    if not databases:
        console.print("[yellow]No databases configured[/yellow]")
        return

    table = Table(title="Configured Databases", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="white")
    table.add_column("Host", style="white")
    table.add_column("Port", style="white")
    table.add_column("Schedule", style="yellow")

    for name, db in databases.items():
        table.add_row(
            name,
            db.get("type", "mysql"),
            db.get("host", "localhost"),
            str(db.get("port", 3306)),
            db.get("backup", {}).get("schedule", "manual"),
        )

    console.print(table)


@cli.command()
def web():
    """Launch web interface"""
    host = _get_config().get_setting("web.host", "localhost")
    port = _get_config().get_setting("web.port", 8501)
    console.print("[bold cyan]Launching web interface...[/bold cyan]")
    console.print(f"[yellow]Open your browser at: http://{host}:{port}[/yellow]")

    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "src/web/app.py",
            "--server.port",
            str(port),
            "--server.address",
            str(host),
        ]
    )
    if result.returncode != 0:
        console.print(f"[red]Streamlit exited with code {result.returncode}[/red]")


@cli.command()
@click.option("--path", help="Path to Apache error log file")
@click.option("--lines", default=50, help="Number of lines to display")
@click.option("--severity", help="Filter by severity (error, warn, info, debug)")
@click.option("--search", help="Search term to filter logs")
def apache_logs(path, lines, severity, search):
    """View Apache error logs"""
    if not path:
        detected = _get_apache_parser().log_paths
        if detected:
            path = detected[0]
            console.print(f"[cyan]Using detected log: {path}[/cyan]")
        else:
            console.print("[red]No Apache logs detected. Please specify --path[/red]")
            return

    if not Path(path).exists():
        console.print(f"[red]Log file not found: {path}[/red]")
        return

    console.print(f"[bold cyan]Apache Error Logs - {path}[/bold cyan]")

    logs = _get_apache_parser().read_logs(path, lines=lines, severity_filter=severity, search_term=search)

    if not logs:
        console.print("[yellow]No log entries found[/yellow]")
        return

    for log in logs[-lines:]:
        severity_level = log.get("severity", "info")

        if severity_level == "error":
            style = "bold red"
            icon = "✗"
        elif severity_level in ["warn", "warning"]:
            style = "bold yellow"
            icon = "⚠"
        else:
            style = "white"
            icon = "•"

        timestamp = log.get("timestamp", "N/A")
        message = log.get("message", log.get("raw", ""))

        console.print(f"[{style}]{icon} [{timestamp}] {message[:200]}[/{style}]")


@cli.command()
@click.option("--path", help="Path to Apache error log file")
def apache_stats(path):
    """Show Apache log statistics"""
    if not path:
        detected = _get_apache_parser().log_paths
        if detected:
            path = detected[0]
            console.print(f"[cyan]Using detected log: {path}[/cyan]")
        else:
            console.print("[red]No Apache logs detected. Please specify --path[/red]")
            return

    stats = _get_apache_parser().get_log_stats(path)

    if not stats["exists"]:
        console.print(f"[red]Log file not found: {path}[/red]")
        return

    table = Table(title=f"Apache Log Statistics - {path}", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("File Size", f"{stats['size_mb']:.2f} MB")
    table.add_row("Total Lines", str(stats["line_count"]))
    table.add_row("Error Count", str(stats["error_count"]))
    table.add_row("Warning Count", str(stats["warning_count"]))
    table.add_row("Last Modified", stats.get("last_modified", "N/A"))
    table.add_row("Readable", "✓" if stats["readable"] else "✗")
    table.add_row("Writable", "✓" if stats["writable"] else "✗")

    console.print(table)


@cli.command()
@click.option("--path", help="Path to Apache error log file")
@click.confirmation_option(prompt="Are you sure you want to clear the log file?")
def clear_apache_log(path):
    """Clear Apache error log file"""
    if not path:
        detected = _get_apache_parser().log_paths
        if detected:
            path = detected[0]
            console.print(f"[cyan]Using detected log: {path}[/cyan]")
        else:
            console.print("[red]No Apache logs detected. Please specify --path[/red]")
            return

    console.print(f"[bold yellow]Clearing log file: {path}[/bold yellow]")

    success, message = _get_apache_parser().clear_log(path)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.option("--path", help="Path to Apache error log file")
@click.option("--format", type=click.Choice(["json", "csv", "txt"]), default="json", help="Export format")
@click.option("--output", help="Output file name")
def export_apache_logs(path, format, output):
    """Export Apache logs to file"""
    if not path:
        detected = _get_apache_parser().log_paths
        if detected:
            path = detected[0]
            console.print(f"[cyan]Using detected log: {path}[/cyan]")
        else:
            console.print("[red]No Apache logs detected. Please specify --path[/red]")
            return

    console.print(f"[bold cyan]Exporting logs from {path} to {format} format...[/bold cyan]")

    success, message = _get_apache_parser().export_logs(path, output_format=format, output_file=output)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.option("--project", help="Verify project backups")
@click.option("--database", help="Verify database backups")
@click.option("--all", is_flag=True, help="Verify all backups for specified type")
@click.option("--fix", is_flag=True, help="Add missing checksums to old backups")
def verify(project, database, all, fix):
    """Verify backup integrity using checksums"""
    if project:
        item_type = "project"
        item_name = project
    elif database:
        item_type = "database"
        item_name = database
    else:
        console.print("[yellow]Please specify --project NAME or --database NAME[/yellow]")
        return

    if fix:
        console.print(f"[bold cyan]Adding checksums to backups for {item_type} '{item_name}'...[/bold cyan]")
        success_count, total_count = _get_backup_engine().backfill_checksums(item_type, item_name)
        console.print(f"[green]✓[/green] Updated {success_count}/{total_count} backups with checksums")
        return

    if all:
        console.print(f"[bold cyan]Verifying all backups for {item_type} '{item_name}'...[/bold cyan]")
        results = _get_backup_engine().verify_all_backups(item_type, item_name)

        if "error" in results:
            console.print(f"[red]✗[/red] {results['error'][1]}")
            return

        success_count = sum(1 for success, _ in results.values() if success)
        total = len(results)

        for backup_name, (success, message) in results.items():
            if success:
                console.print(f"[green]✓[/green] {backup_name}: {message}")
            else:
                console.print(f"[red]✗[/red] {backup_name}: {message}")

        console.print(f"\n[bold]Summary: {success_count}/{total} backups verified successfully[/bold]")
    else:
        console.print("[yellow]Please add --all to verify all backups or specify a specific backup file[/yellow]")


@cli.command()
@click.option("--all", is_flag=True, help="Backfill checksums for all projects and databases")
@click.option("--projects", is_flag=True, help="Backfill checksums for all projects")
@click.option("--databases", is_flag=True, help="Backfill checksums for all databases")
def backfill_checksums(all, projects, databases):
    """Add checksums to old backups that don't have them"""
    total_updated = 0
    total_backups = 0

    if all or projects:
        console.print("[bold cyan]Backfilling checksums for all project backups...[/bold cyan]")
        for name in _get_config().get_all_projects():
            updated, total = _get_backup_engine().backfill_checksums("project", name)
            total_updated += updated
            total_backups += total
            if updated > 0:
                console.print(f"[green]✓[/green] {name}: Updated {updated}/{total} backups")

    if all or databases:
        console.print("[bold cyan]Backfilling checksums for all database backups...[/bold cyan]")
        for name in _get_config().get_all_databases():
            updated, total = _get_backup_engine().backfill_checksums("database", name)
            total_updated += updated
            total_backups += total
            if updated > 0:
                console.print(f"[green]✓[/green] {name}: Updated {updated}/{total} backups")

    if not (all or projects or databases):
        console.print("[yellow]Please specify --all, --projects, or --databases[/yellow]")
        return

    console.print(f"\n[bold green]✓ Total: Updated {total_updated}/{total_backups} backups with checksums[/bold green]")


@cli.command()
@click.option("--project", help="Project name")
@click.option("--database", help="Database name")
@click.argument("backup_file")
@click.option("--tags", "-t", multiple=True, help="Add tags (can be used multiple times)")
@click.option("--importance", type=click.Choice(["critical", "high", "normal", "low"]), help="Set importance level")
@click.option("--pin", is_flag=True, help="Pin backup to preserve forever")
@click.option("--description", "-d", help="Add or update description")
def tag(project, database, backup_file, tags, importance, pin, description):
    """Tag a backup for preservation and organization"""
    if project:
        item_type = "project"
        item_name = project
    elif database:
        item_type = "database"
        item_name = database
    else:
        console.print("[yellow]Please specify --project NAME or --database NAME[/yellow]")
        return

    console.print(f"[bold cyan]Tagging backup '{backup_file}' for {item_type} '{item_name}'...[/bold cyan]")

    success, message = _get_backup_engine().tag_backup(
        item_type=item_type,
        item_name=item_name,
        backup_file=backup_file,
        tags=list(tags) if tags else None,
        importance=importance,
        keep_forever=pin,
        description=description,
    )

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.option("--type", "item_type", type=click.Choice(["project", "database"]), help="Filter by type")
@click.option("--name", "item_name", help="Filter by specific project/database name")
def list_tagged(item_type, item_name):
    """List all tagged backups"""
    console.print("[bold cyan]Tagged Backups[/bold cyan]\n")

    tagged = _get_backup_engine().list_tagged_backups(item_type, item_name)

    if not tagged:
        console.print("[yellow]No tagged backups found[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Type", style="cyan", width=10)
    table.add_column("Name", style="white", width=20)
    table.add_column("Backup File", style="white")
    table.add_column("Tags", style="green")
    table.add_column("Importance", style="yellow")
    table.add_column("Status", style="red")
    table.add_column("Size", justify="right")

    for backup in tagged:
        tags_str = ", ".join(backup.get("tags", [])) if backup.get("tags") else "-"
        importance = backup.get("importance", "normal")

        status = []
        if backup.get("keep_forever") or backup.get("pinned"):
            status.append("📌 Pinned")
        if backup.get("importance") in ["critical", "high"]:
            status.append("⚠️ Important")

        table.add_row(
            backup.get("item_type", "unknown"),
            backup.get("item_name", "unknown"),
            backup.get("backup_name", "unknown"),
            tags_str,
            importance,
            " ".join(status) if status else "-",
            f"{backup.get('size_mb', 0):.2f} MB",
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(tagged)} tagged backups[/dim]")


@cli.command()
@click.option("--list", "list_schedules", is_flag=True, help="List all backup schedules")
@click.option("--add", "add_schedule", is_flag=True, help="Add a new backup schedule")
@click.option("--remove", "remove_pattern", help="Remove schedules matching pattern")
@click.option("--setup-defaults", is_flag=True, help="Setup default backup schedules")
@click.option(
    "--type",
    "backup_type",
    type=click.Choice(["project", "database", "snapshot", "all-projects", "all-databases"]),
    help="Backup type for new schedule",
)
@click.option("--target", help="Project or database name for new schedule")
@click.option("--schedule", help='Cron schedule (e.g., "0 2 * * *" for daily at 2 AM)')
@click.option(
    "--template",
    type=click.Choice(["hourly", "daily", "weekly", "monthly", "twice_daily"]),
    help="Use a schedule template",
)
def schedule(list_schedules, add_schedule, remove_pattern, setup_defaults, backup_type, target, schedule, template):
    """Manage automated backup schedules"""

    if list_schedules:
        console.print("[bold cyan]Current Backup Schedules[/bold cyan]\n")

        schedules = _get_scheduler().list_backup_schedules()

        if not schedules:
            console.print("[yellow]No backup schedules configured[/yellow]")
            console.print("[dim]Tip: Use 'qm schedule --setup-defaults' to add default schedules[/dim]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Schedule", style="cyan")
        table.add_column("Command", style="white")
        table.add_column("When", style="green")
        table.add_column("Description", style="yellow")

        for sched in schedules:
            table.add_row(
                sched["schedule"],
                sched["command"][:50] + "..." if len(sched["command"]) > 50 else sched["command"],
                sched["human_readable"],
                sched["comment"],
            )

        console.print(table)

    elif add_schedule:
        if not backup_type or not target:
            console.print("[red]Please provide --type and --target for the backup[/red]")
            return

        # Determine schedule
        if template:
            templates = _get_scheduler().create_schedule_templates()
            if template in templates:
                cron_schedule = templates[template]["schedule"]
                console.print(f"[cyan]Using template '{template}': {templates[template]['description']}[/cyan]")
            else:
                console.print(f"[red]Invalid template: {template}[/red]")
                return
        elif schedule:
            cron_schedule = schedule
        else:
            console.print("[red]Please provide either --schedule or --template[/red]")
            return

        # Generate command
        try:
            command = _get_scheduler().generate_backup_command(backup_type, target)
            comment = f"{backup_type.title()} backup of {target}"

            success, msg = _get_scheduler().add_backup_schedule(cron_schedule, command, comment)

            if success:
                console.print(f"[green]✓[/green] {msg}")
                console.print(
                    f"[dim]Schedule: {cron_schedule} ({_get_scheduler().parse_cron_schedule(cron_schedule)})[/dim]"
                )
                console.print(f"[dim]Command: {command}[/dim]")
            else:
                console.print(f"[red]✗[/red] {msg}")

        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")

    elif remove_pattern:
        console.print(f"[yellow]Removing schedules matching: {remove_pattern}[/yellow]")
        success, msg = _get_scheduler().remove_backup_schedule(remove_pattern)

        if success:
            console.print(f"[green]✓[/green] {msg}")
        else:
            console.print(f"[red]✗[/red] {msg}")

    elif setup_defaults:
        console.print("[bold cyan]Setting up default backup schedules...[/bold cyan]")

        # Get all projects and databases
        projects = list(_get_config().get_all_projects().keys())
        databases = list(_get_config().get_all_databases().keys())

        important = _get_config().get_setting("scheduler.important_projects", [])
        success, msg = _get_scheduler().setup_default_schedules(projects, databases, important)

        if success:
            console.print(f"[green]✓[/green] {msg}")
            console.print("\n[dim]Run 'qm schedule --list' to view the schedules[/dim]")
        else:
            console.print(f"[red]✗[/red] {msg}")

    else:
        # Show templates
        console.print("[bold cyan]Available Schedule Templates[/bold cyan]\n")

        templates = _get_scheduler().create_schedule_templates()
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Template", style="cyan")
        table.add_column("Schedule", style="white")
        table.add_column("Description", style="green")

        for name, info in templates.items():
            table.add_row(name, info["schedule"], info["description"])

        console.print(table)
        console.print("\n[dim]Usage: qm schedule --add --type project --target myproject --template daily[/dim]")


@cli.command()
@click.option("--project", help="Project name to check for PHP logs")
@click.option("--system", is_flag=True, help="Show system PHP logs")
@click.option("--lines", default=50, help="Number of lines to display")
@click.option(
    "--level", type=click.Choice(["fatal", "error", "warning", "notice", "deprecated"]), help="Filter by severity level"
)
@click.option("--search", help="Search term to filter logs")
@click.option("--summary", is_flag=True, help="Show error summary instead of individual errors")
def php_logs(project, system, lines, level, search, summary):
    """View and analyze PHP error logs"""

    if project:
        # Find project-specific PHP logs
        project_config = _get_config().get_project(project)
        if not project_config:
            console.print(f"[red]Project '{project}' not found[/red]")
            return

        found_logs = _get_php_parser().find_project_logs(project_config["path"])
        all_logs = []
        for category, paths in found_logs.items():
            if paths:
                console.print(f"[cyan]Found {category} logs:[/cyan]")
                for path in paths:
                    console.print(f"  - {path}")
                    all_logs.extend(paths)

        if not all_logs:
            console.print(f"[yellow]No PHP logs found in project '{project}'[/yellow]")
            return

        # Use the first log file found (or most recent)
        log_path = all_logs[0]
    elif system:
        # Use system PHP logs
        if _get_php_parser().log_locations["system"]:
            log_path = _get_php_parser().log_locations["system"][0]
            console.print(f"[cyan]Using system log: {log_path}[/cyan]")
        else:
            console.print("[red]No system PHP logs found[/red]")
            return
    else:
        # Show detected logs
        console.print("[bold cyan]Detected PHP Log Locations[/bold cyan]\n")

        locations = _get_php_parser().log_locations

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Location", style="white")

        for log_type, paths in locations.items():
            for path in paths:
                table.add_row(log_type.title(), path)

        console.print(table)
        console.print("\n[dim]Use --project NAME or --system to view specific logs[/dim]")
        return

    if summary:
        # Show error summary
        console.print("[bold cyan]PHP Error Summary - Last 24 Hours[/bold cyan]")
        console.print(f"[dim]Log: {log_path}[/dim]\n")

        summary_data = _get_php_parser().get_error_summary(log_path, last_hours=24)

        # Statistics table
        stats_table = Table(show_header=True, header_style="bold magenta")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Count", style="white", justify="right")

        stats_table.add_row("Total Errors", str(summary_data["total_errors"]))
        stats_table.add_row("Fatal Errors", f"[red]{summary_data['fatal_errors']}[/red]")
        stats_table.add_row("Warnings", f"[yellow]{summary_data['warnings']}[/yellow]")
        stats_table.add_row("Notices", f"[blue]{summary_data['notices']}[/blue]")
        stats_table.add_row("Exceptions", str(summary_data["exceptions"]))

        console.print(stats_table)

        # Most common errors
        if summary_data["most_common"]:
            console.print("\n[bold]Most Common Errors:[/bold]")
            for i, error_info in enumerate(summary_data["most_common"], 1):
                console.print(f"{i}. {error_info['error']} ({error_info['count']} times)")

        # Recent fatal errors
        if summary_data["recent_fatal"]:
            console.print("\n[bold red]Recent Fatal Errors:[/bold red]")
            for error in summary_data["recent_fatal"][:3]:
                console.print(f"[red]• [{error.get('timestamp', 'N/A')}] {error.get('message', '')[:100]}[/red]")
                if "file" in error:
                    console.print(f"  [dim]{error['file']}:{error.get('line', '?')}[/dim]")
    else:
        # Show individual log entries
        console.print(f"[bold cyan]PHP Error Logs - {log_path}[/bold cyan]")

        logs = _get_php_parser().read_php_logs(log_path, lines=lines, level_filter=level, search_term=search)

        if not logs:
            console.print("[yellow]No log entries found matching criteria[/yellow]")
            return

        for log in logs[-lines:]:
            # Color-code by severity
            level_name = log.get("level", "info")

            if level_name in ["fatal", "critical", "emergency"]:
                style = "bold red"
                icon = "✗"
            elif level_name in ["error", "alert"]:
                style = "red"
                icon = "✗"
            elif level_name in ["warning", "warn"]:
                style = "yellow"
                icon = "⚠"
            elif level_name in ["notice", "deprecated"]:
                style = "blue"
                icon = "ℹ"
            else:
                style = "white"
                icon = "•"

            timestamp = log.get("timestamp", "N/A")
            message = log.get("message", log.get("raw", ""))[:200]

            console.print(f"[{style}]{icon} [{timestamp}] [{level_name.upper()}] {message}[/{style}]")

            # Show file and line if available
            if log.get("file"):
                console.print(f"  [dim]📁 {log['file']}:{log.get('line', '?')}[/dim]")

            # Show stack trace preview if available
            if "stack_trace" in log:
                console.print(f"  [dim]📚 Stack trace: {log['stack_trace']['frame_count']} frames[/dim]")


@cli.command()
@click.option("--project", help="Project to analyze")
@click.option("--format", type=click.Choice(["html", "json", "txt"]), default="html", help="Report format")
@click.option("--output", help="Output file name")
@click.option("--hours", default=24, help="Hours to include in report")
def php_report(project, format, output, hours):
    """Generate PHP error report"""
    console.print("[bold cyan]Generating PHP Error Report...[/bold cyan]")

    log_paths = []

    if project:
        project_config = _get_config().get_project(project)
        if project_config:
            found_logs = _get_php_parser().find_project_logs(project_config["path"])
            for paths in found_logs.values():
                log_paths.extend(paths)
    else:
        # Include all detected logs
        for paths in _get_php_parser().log_locations.values():
            log_paths.extend(paths)

    if not log_paths:
        console.print("[yellow]No PHP logs found to analyze[/yellow]")
        return

    console.print(f"[cyan]Analyzing {len(log_paths)} log files...[/cyan]")

    success, message = _get_php_parser().export_error_report(log_paths, output, format, hours)

    if success:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[red]✗[/red] {message}")


@cli.command()
@click.option("--detailed", is_flag=True, help="Show detailed analysis by item")
@click.option("--cleanup", is_flag=True, help="Show cleanup candidates")
@click.option("--timeline", is_flag=True, help="Show storage growth timeline")
@click.option("--export", help="Export report to file (json/html/txt)")
def storage(detailed, cleanup, timeline, export):
    """Analyze backup storage usage and find cleanup opportunities"""

    console.print("[bold cyan]Storage Analysis Report[/bold cyan]\n")

    # Get overall usage
    usage = _get_storage_analyzer().get_total_usage()

    # Overall statistics table
    stats_table = Table(title="Storage Overview", show_header=True, header_style="bold magenta")
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="white", justify="right")

    stats_table.add_row("Total Storage Used", f"{usage['total_size_gb']:.2f} GB")
    stats_table.add_row("Total Files", str(usage["file_count"]))
    stats_table.add_row("Backup Count", str(usage["backup_count"]))
    stats_table.add_row("Disk Usage", f"{usage['disk_percent']:.1f}%")
    stats_table.add_row("Disk Free Space", f"{usage['disk_free'] / (1024**3):.2f} GB")

    console.print(stats_table)

    # Storage by type
    by_type = _get_storage_analyzer().analyze_by_type()

    type_table = Table(title="\nStorage by Type", show_header=True, header_style="bold magenta")
    type_table.add_column("Type", style="cyan")
    type_table.add_column("Size", justify="right")
    type_table.add_column("Count", justify="right")
    type_table.add_column("Percentage", justify="right")

    type_table.add_row(
        "Projects",
        f"{by_type.get('projects', {}).get('size_gb', 0):.2f} GB",
        str(by_type.get("projects", {}).get("count", 0)),
        f"{by_type.get('projects', {}).get('percentage', 0):.1f}%",
    )
    type_table.add_row(
        "Databases",
        f"{by_type.get('databases', {}).get('size_gb', 0):.2f} GB",
        str(by_type.get("databases", {}).get("count", 0)),
        f"{by_type.get('databases', {}).get('percentage', 0):.1f}%",
    )

    console.print(type_table)

    if detailed:
        # Detailed analysis by item
        console.print("\n[bold]Storage by Item (Top 10)[/bold]")

        items = _get_storage_analyzer().analyze_by_item()[:10]

        item_table = Table(show_header=True, header_style="bold magenta")
        item_table.add_column("Name", style="cyan")
        item_table.add_column("Type", style="white")
        item_table.add_column("Size", justify="right")
        item_table.add_column("Backups", justify="right")
        item_table.add_column("Tagged", justify="right")
        item_table.add_column("Avg Size", justify="right")

        for item in items:
            item_table.add_row(
                item["name"],
                item["type"],
                f"{item['total_size_mb']:.1f} MB",
                str(item["backup_count"]),
                str(item["tagged_count"]),
                f"{item['average_size_mb']:.1f} MB",
            )

        console.print(item_table)

    if cleanup:
        # Show cleanup candidates
        console.print("\n[bold]Cleanup Candidates[/bold]")

        candidates = _get_storage_analyzer().get_cleanup_candidates()

        if candidates["total_count"] > 0:
            console.print(f"\n[yellow]Found {candidates['total_count']} backups that can be cleaned up[/yellow]")
            console.print(f"[yellow]Total space to recover: {candidates['total_size_gb']:.2f} GB[/yellow]\n")

            cleanup_table = Table(show_header=True, header_style="bold magenta")
            cleanup_table.add_column("Item", style="cyan")
            cleanup_table.add_column("Backup", style="white")
            cleanup_table.add_column("Size", justify="right")
            cleanup_table.add_column("Age", justify="right")
            cleanup_table.add_column("Reason", style="yellow")

            # Show first 20 candidates
            all_candidates = candidates["projects"] + candidates["databases"]
            for candidate in all_candidates[:20]:
                cleanup_table.add_row(
                    candidate["item_name"],
                    candidate["name"][:30] + "..." if len(candidate["name"]) > 30 else candidate["name"],
                    f"{candidate['size_mb']:.1f} MB",
                    f"{candidate['age_days']} days",
                    candidate["reason"],
                )

            console.print(cleanup_table)

            if len(all_candidates) > 20:
                console.print(f"\n[dim]... and {len(all_candidates) - 20} more[/dim]")
        else:
            console.print("[green]No cleanup candidates found. Storage is well-maintained![/green]")

    if timeline:
        # Show storage timeline
        console.print("\n[bold]Storage Growth Timeline (Last 30 Days)[/bold]")

        timeline_data = _get_storage_analyzer().get_storage_timeline(30)

        # Show only weekly snapshots for brevity
        weekly_data = [timeline_data[i] for i in range(0, len(timeline_data), 7)]

        timeline_table = Table(show_header=True, header_style="bold magenta")
        timeline_table.add_column("Date", style="cyan")
        timeline_table.add_column("Total Size", justify="right")
        timeline_table.add_column("Projects", justify="right")
        timeline_table.add_column("Databases", justify="right")

        for day in weekly_data:
            timeline_table.add_row(
                day["date"], f"{day['total_size_mb']:.1f} MB", str(day["projects_count"]), str(day["databases_count"])
            )

        console.print(timeline_table)

    # Show duplication analysis
    duplication = _get_storage_analyzer().get_duplication_analysis()
    if duplication["total_potential_savings_mb"] > 100:
        console.print(
            f"\n[yellow]⚠ Potential savings from deduplication: {duplication['total_potential_savings_mb']:.0f} MB[/yellow]"
        )

    # Recommendations
    report = _get_storage_analyzer().generate_cleanup_report(dry_run=True)
    if report["recommendations"]:
        console.print("\n[bold]Recommendations:[/bold]")
        for rec in report["recommendations"]:
            if rec["level"] == "critical":
                style = "bold red"
                icon = "🔴"
            elif rec["level"] == "high":
                style = "yellow"
                icon = "🟡"
            else:
                style = "white"
                icon = "🔵"

            console.print(f"{icon} [{style}]{rec['message']}[/{style}]")

    if export:
        # Export report
        import json

        if export.endswith(".json"):
            with open(export, "w") as f:
                json.dump(report, f, indent=2, default=str)
            console.print(f"\n[green]✓[/green] Report exported to {export}")
        else:
            console.print("[red]Unsupported export format. Use .json extension[/red]")


@cli.command()
@click.option("--dry-run", is_flag=True, default=True, help="Preview cleanup without deleting")
@click.option("--force", is_flag=True, help="Actually perform cleanup (dangerous!)")
@click.option("--retention-days", default=DEFAULT_PROJECT_RETENTION_DAYS, help="Custom retention days")
@click.option("--preserve-tagged", is_flag=True, default=True, help="Preserve tagged backups")
def cleanup(dry_run, force, retention_days, preserve_tagged):
    """Clean up old backups to free storage space"""

    if force and not dry_run:
        console.print("[bold red]⚠ WARNING: This will permanently delete backup files![/bold red]")
        if not click.confirm("Are you sure you want to proceed?"):
            console.print("[yellow]Cleanup cancelled[/yellow]")
            return

    console.print("[bold cyan]Backup Cleanup Analysis[/bold cyan]\n")

    # Get cleanup candidates (use config defaults if user didn't override)
    default_project_days = _get_config().get_setting("defaults.project.retention_days", 30)
    default_db_days = _get_config().get_setting("defaults.database.retention_days", 14)
    retention = {
        "project": retention_days if retention_days != 30 else default_project_days,
        "database": retention_days if retention_days != 30 else default_db_days,
    }
    candidates = _get_storage_analyzer().get_cleanup_candidates(
        retention_days=retention, preserve_tagged=preserve_tagged
    )

    if candidates["total_count"] == 0:
        console.print("[green]✓ No backups need cleanup. Storage is optimized![/green]")
        return

    console.print(f"[yellow]Found {candidates['total_count']} backups to clean up[/yellow]")
    console.print(f"[yellow]Space to recover: {candidates['total_size_gb']:.2f} GB[/yellow]\n")

    # Show summary by item
    item_summary = {}
    for candidate in candidates["projects"] + candidates["databases"]:
        item_name = candidate["item_name"]
        if item_name not in item_summary:
            item_summary[item_name] = {"count": 0, "size": 0}
        item_summary[item_name]["count"] += 1
        item_summary[item_name]["size"] += candidate["size"]

    summary_table = Table(title="Cleanup Summary by Item", show_header=True, header_style="bold magenta")
    summary_table.add_column("Item", style="cyan")
    summary_table.add_column("Backups to Delete", justify="right")
    summary_table.add_column("Space to Free", justify="right")

    for item_name, data in sorted(item_summary.items(), key=lambda x: x[1]["size"], reverse=True):
        summary_table.add_row(item_name, str(data["count"]), f"{data['size'] / (1024**2):.1f} MB")

    console.print(summary_table)

    if not force or dry_run:
        console.print("\n[dim]This is a dry run. No files will be deleted.[/dim]")
        console.print("[dim]Use --force --no-dry-run to actually delete files.[/dim]")
    else:
        # Perform actual cleanup
        console.print("\n[red]Starting cleanup...[/red]")

        deleted_count = 0
        deleted_size = 0

        with console.status("[yellow]Deleting old backups...[/yellow]"):
            for candidate in candidates["projects"] + candidates["databases"]:
                try:
                    file_path = Path(candidate["path"])
                    if file_path.exists():
                        file_path.unlink()
                        deleted_count += 1
                        deleted_size += candidate["size"]

                        # Also delete metadata file
                        metadata_path = file_path.parent / file_path.name.replace(".tar.gz", ".json").replace(
                            ".sql.gz", ".json"
                        )
                        if metadata_path.exists():
                            metadata_path.unlink()

                        console.print(f"[green]✓[/green] Deleted {candidate['name']}")

                except Exception as e:
                    console.print(f"[red]✗[/red] Failed to delete {candidate['name']}: {e}")

        console.print("\n[green]✓ Cleanup complete![/green]")
        console.print(f"[green]Deleted {deleted_count} backups, freed {deleted_size / (1024**3):.2f} GB[/green]")


@cli.command()
@click.option("--status", is_flag=True, help="Show current retention status")
@click.option("--apply", is_flag=True, help="Apply tiered retention")
@click.option("--optimize-all", is_flag=True, help="Optimize retention for all items")
@click.option("--suggest", help="Suggest optimal retention for an item")
@click.option("--dry-run", is_flag=True, default=True, help="Preview changes without deleting")
@click.option("--force", is_flag=True, help="Actually apply retention (delete files)")
def retention(status, apply, optimize_all, suggest, dry_run, force):
    """Manage tiered backup retention (hourly→daily→weekly→monthly)"""

    if status:
        console.print("[bold cyan]Retention Status Overview[/bold cyan]\n")

        status_data = _get_retention_manager().get_retention_status()

        # Overall statistics
        stats_table = Table(title="Overall Statistics", show_header=True, header_style="bold magenta")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", justify="right")

        stats_table.add_row("Total Backups", str(status_data["total_backups"]))
        stats_table.add_row("Total Size", f"{status_data['total_size'] / (1024**3):.2f} GB")

        console.print(stats_table)

        # Tier distribution
        tier_table = Table(title="\nBackup Distribution by Tier", show_header=True, header_style="bold magenta")
        tier_table.add_column("Tier", style="cyan")
        tier_table.add_column("Count", justify="right")

        for tier_name in ["hourly", "daily", "weekly", "monthly", "yearly"]:
            count = status_data["tier_distribution"].get(tier_name, 0)
            tier_table.add_row(tier_name.title(), str(count))

        console.print(tier_table)

        # Per-item breakdown
        if status_data["items"]:
            console.print("\n[bold]Per-Item Retention Status:[/bold]")

            item_table = Table(show_header=True, header_style="bold magenta")
            item_table.add_column("Item", style="cyan")
            item_table.add_column("Type", style="white")
            item_table.add_column("Total", justify="right")
            item_table.add_column("Hourly", justify="right")
            item_table.add_column("Daily", justify="right")
            item_table.add_column("Weekly", justify="right")
            item_table.add_column("Monthly", justify="right")

            for item in status_data["items"][:20]:
                item_table.add_row(
                    item["name"],
                    item["type"],
                    str(item["total_backups"]),
                    str(item["tiers"].get("hourly", 0)),
                    str(item["tiers"].get("daily", 0)),
                    str(item["tiers"].get("weekly", 0)),
                    str(item["tiers"].get("monthly", 0)),
                )

            console.print(item_table)

    elif suggest:
        # Parse item type and name
        parts = suggest.split("/")
        if len(parts) != 2 or parts[0] not in ["project", "database"]:
            console.print("[red]Invalid format. Use: project/name or database/name[/red]")
            return

        item_type, item_name = parts
        console.print(f"[bold cyan]Analyzing retention for {item_type} '{item_name}'...[/bold cyan]\n")

        suggestion = _get_retention_manager().suggest_tier_configuration(item_type, item_name)

        if "error" in suggestion:
            console.print(f"[red]Error: {suggestion['error']}[/red]")
            return

        # Show analysis
        console.print(f"Current backups: {suggestion['current_backups']}")
        console.print(f"Current size: {suggestion['current_size_mb']:.1f} MB")
        console.print(f"Average backup interval: {suggestion['avg_backup_interval_hours']:.1f} hours\n")

        console.print("[bold]Suggested Retention Tiers:[/bold]")
        for tier_name, tier_config in suggestion["suggested_tiers"].items():
            console.print(f"  {tier_name}: Keep {tier_config['keep']} backups")

        console.print("\n[green]After optimization:[/green]")
        console.print(f"  Backups to keep: {suggestion['backups_after']}")
        console.print(f"  Size after: {suggestion['size_after_mb']:.1f} MB")
        console.print(f"  Space savings: {suggestion['space_savings_mb']:.1f} MB")

    elif optimize_all:
        if force and not dry_run:
            console.print("[bold red]⚠ WARNING: This will delete backups according to retention tiers![/bold red]")
            if not click.confirm("Are you sure you want to proceed?"):
                console.print("[yellow]Operation cancelled[/yellow]")
                return

        console.print("[bold cyan]Optimizing Retention for All Items[/bold cyan]\n")

        results = _get_retention_manager().optimize_all_retention(dry_run=(not force or dry_run))

        # Show results
        total_freed_mb = results["total_space_freed"] / (1024 * 1024)

        if dry_run or not force:
            console.print("[yellow]DRY RUN - No files deleted[/yellow]\n")
        else:
            console.print("[green]Optimization complete![/green]\n")

        # Summary table
        summary_table = Table(title="Optimization Summary", show_header=True, header_style="bold magenta")
        summary_table.add_column("Category", style="cyan")
        summary_table.add_column("Items", justify="right")
        summary_table.add_column("Files Deleted", justify="right")
        summary_table.add_column("Space Freed", justify="right")

        project_deleted = sum(r.get("backups_to_delete", 0) for r in results["projects"].values())
        db_deleted = sum(r.get("backups_to_delete", 0) for r in results["databases"].values())

        summary_table.add_row("Projects", str(len(results["projects"])), str(project_deleted), "")
        summary_table.add_row("Databases", str(len(results["databases"])), str(db_deleted), "")
        summary_table.add_row("[bold]Total", "", str(project_deleted + db_deleted), f"{total_freed_mb:.1f} MB")

        console.print(summary_table)

        # Show details for items with significant changes
        significant_items = []
        for name, report in results["projects"].items():
            if report.get("backups_to_delete", 0) > 5:
                significant_items.append((name, "project", report))

        for name, report in results["databases"].items():
            if report.get("backups_to_delete", 0) > 5:
                significant_items.append((name, "database", report))

        if significant_items:
            console.print("\n[bold]Significant Changes:[/bold]")
            for name, item_type, report in significant_items[:10]:
                console.print(
                    f"  {name} ({item_type}): {report['backups_to_delete']} deleted, {report['backups_to_keep']} kept"
                )

    else:
        # Show help
        console.print("[bold cyan]Tiered Retention System[/bold cyan]\n")
        console.print("This system automatically manages backup lifecycle using tiers:")
        console.print("  • [cyan]Hourly[/cyan]: Keep recent backups (last 24 hours)")
        console.print("  • [cyan]Daily[/cyan]: Keep daily backups (last week)")
        console.print("  • [cyan]Weekly[/cyan]: Keep weekly backups (last month)")
        console.print("  • [cyan]Monthly[/cyan]: Keep monthly backups (last year)")
        console.print("  • [cyan]Yearly[/cyan]: Keep yearly backups (5 years)\n")

        console.print("[bold]Commands:[/bold]")
        console.print("  qm retention --status           # View current retention status")
        console.print("  qm retention --optimize-all     # Preview optimization")
        console.print("  qm retention --optimize-all --force --no-dry-run  # Apply optimization")
        console.print("  qm retention --suggest project/myproject  # Get suggestions")


@cli.command()
@click.argument("project")
@click.option("--message", "-m", help="Description/commit message for the snapshot")
@click.option("--skip-databases", is_flag=True, help="Skip backing up associated databases")
def snapshot(project, message, skip_databases):
    """Create a complete snapshot: Git commit + project backup + database backups (if configured)

    This is a convenient command for daily workflow that combines:
    - Git savepoint (if project is a Git repo)
    - Project backup
    - Associated database backups (if configured in project settings)
    """
    console.print(f"[bold cyan]Creating complete snapshot for '{project}'...[/bold cyan]")
    if message:
        console.print(f"[dim]Message: {message}[/dim]")

    # Use the new quick_snapshot method
    results = _get_backup_engine().quick_snapshot(
        project_name=project, message=message, backup_databases=not skip_databases
    )

    # Check for error
    if "error" in results:
        console.print(f"[red]✗ {results['error'][1]}[/red]")
        return

    # Display results for each operation
    console.print()

    # Git savepoint
    if "git_savepoint" in results:
        success, msg = results["git_savepoint"]
        if success:
            console.print(f"[green]✓[/green] Git: {msg}")
        else:
            console.print(f"[yellow]⚠[/yellow] Git: {msg}")

    # Project backup
    if "project_backup" in results:
        success, msg = results["project_backup"]
        if success:
            console.print(f"[green]✓[/green] Project Backup: {msg}")
        else:
            console.print(f"[red]✗[/red] Project Backup: {msg}")

    # Database backups
    if "database_backups" in results:
        db_results = results["database_backups"]
        if isinstance(db_results, dict):
            console.print("\n[bold]Database Backups:[/bold]")
            for db_name, (success, msg) in db_results.items():
                if success:
                    console.print(f"[green]✓[/green] {db_name}: {msg}")
                else:
                    console.print(f"[red]✗[/red] {db_name}: {msg}")
        else:
            success, msg = db_results
            if not success:
                console.print(f"[dim]ℹ Databases: {msg}[/dim]")

    # Summary
    if "summary" in results:
        success, msg = results["summary"]
        console.print()
        console.print(f"[bold green]✓ {msg}[/bold green]")


if __name__ == "__main__":
    cli()
