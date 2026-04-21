"""Interactive guided setup for Quartermaster (qm init)."""

import os
import shutil
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.discovery import (
    build_project_config,
    detect_claude_dirs,
    detect_environment,
    get_scan_roots,
    scan_for_databases,
    scan_for_projects,
)

console = Console()

# Paths relative to the project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


def _ensure_configs() -> bool:
    """Copy example configs if production configs don't exist.

    Returns:
        True if configs are ready, False on error.
    """
    ready = True
    for name in ("settings.yaml", "projects.yaml", "databases.yaml"):
        target = _CONFIG_DIR / name
        example = _CONFIG_DIR / f"{name}.example"
        if not target.exists():
            if example.exists():
                shutil.copy2(example, target)
                console.print(f"  Created [cyan]{name}[/] from example")
            else:
                console.print(f"  [red]Missing {name}.example, cannot create config[/]")
                ready = False
    return ready


def _update_setting(key_path: str, value: str | list[str]) -> None:
    """Update a dotted key in settings.yaml (e.g. 'storage.local_base')."""
    settings_file = _CONFIG_DIR / "settings.yaml"
    with open(settings_file) as f:
        settings = yaml.safe_load(f) or {}

    keys = key_path.split(".")
    node = settings
    for k in keys[:-1]:
        if k not in node or not isinstance(node[k], dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value

    tmp_file = settings_file.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_file, settings_file)


def _parse_selection(raw: str, count: int) -> list[int] | None:
    """Parse a user selection string into 0-based indices.

    Accepts 'all', 'none', or comma-separated 1-based numbers.
    Returns None for 'none' or invalid input.
    """
    text = raw.strip().lower()
    if text == "none":
        return None
    if text == "all":
        return list(range(count))
    try:
        indices = [int(s.strip()) - 1 for s in text.split(",")]
        return [i for i in indices if 0 <= i < count] or None
    except ValueError:
        console.print("  [yellow]Invalid selection, skipping.[/]")
        return None


def _step_storage(non_interactive: bool) -> str:
    """Configure backup storage path. Returns the chosen path."""
    console.print("\n[bold cyan]Step 1: Backup Storage[/]")
    console.print("  Where should Quartermaster store backups?\n")

    default = "~/backups/qm"

    if non_interactive:
        path = default
    else:
        path = click.prompt("  Storage path", default=default, type=str)

    expanded = str(Path(path).expanduser())
    Path(expanded).mkdir(parents=True, exist_ok=True)

    _update_setting("storage.local_base", path)
    console.print(f"  [green]\u2713[/] Storage set to [cyan]{path}[/]")
    return expanded


def _step_projects(non_interactive: bool) -> int:
    """Discover and configure projects. Returns count added."""
    console.print("\n[bold cyan]Step 2: Project Discovery[/]")

    env = detect_environment()
    roots = get_scan_roots(env)

    if not roots:
        console.print("  No common project directories found. Add projects manually later.")
        return 0

    console.print(f"  Scanning {len(roots)} directories...")
    projects = scan_for_projects(roots)

    if not projects:
        console.print("  No projects found. Add them to [cyan]config/projects.yaml[/] later.")
        return 0

    # Display discovered projects
    table = Table(title=f"Found {len(projects)} projects")
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Path", style="dim")

    for i, proj in enumerate(projects, 1):
        table.add_row(str(i), proj["name"], proj["type"], proj["path"])

    console.print(table)

    if non_interactive:
        console.print("  Non-interactive mode: skipping project selection.")
        return 0

    console.print("\n  Enter project numbers to add (comma-separated), 'all', or 'none':")
    selection = click.prompt("  Select", default="none", type=str)
    indices = _parse_selection(selection, len(projects))
    if indices is None:
        return 0

    from core.config_manager import ConfigManager
    config = ConfigManager()

    added = 0
    existing = config.get_all_projects()
    for idx in indices:
        proj = projects[idx]
        name = proj["name"]
        if name in existing:
            console.print(f"  [yellow]Skipping {name} (already configured)[/]")
            continue
        proj_config = build_project_config(proj)
        config.add_project(name, proj_config)
        console.print(f"  [green]\u2713[/] Added [cyan]{name}[/] ({proj['type']})")
        added += 1

    return added


def _step_databases(non_interactive: bool) -> int:
    """Discover and configure databases. Returns count added."""
    console.print("\n[bold cyan]Step 3: Database Discovery[/]")

    databases = scan_for_databases()

    if not databases:
        console.print("  No MySQL/MariaDB instance detected. Skipping.")
        return 0

    console.print(f"  Found MySQL with {len(databases)} user databases:")
    for i, db in enumerate(databases, 1):
        console.print(f"    [cyan]{i}.[/] {db['name']}")

    if non_interactive:
        console.print("  Non-interactive mode: skipping database selection.")
        return 0

    console.print("\n  Enter database numbers to add (comma-separated), 'all', or 'none':")
    selection = click.prompt("  Select", default="none", type=str)
    indices = _parse_selection(selection, len(databases))
    if indices is None:
        return 0

    # Prompt for credentials
    console.print("\n  Database credentials (used for mysqldump):")
    db_user = click.prompt("  MySQL user", default="root", type=str)
    db_pass = click.prompt("  MySQL password", default="", hide_input=True, type=str)

    from core.config_manager import ConfigManager
    config = ConfigManager()

    added = 0
    existing = config.get_all_databases()
    for idx in indices:
        db = databases[idx]
        name = db["name"]
        if name in existing:
            console.print(f"  [yellow]Skipping {name} (already configured)[/]")
            continue

        db_config = {
            "type": "mysql",
            "host": db["host"],
            "port": db["port"],
            "user": db_user,
            "password": db_pass,
            "description": name,
            "backup": {
                "enabled": True,
                "schedule": "daily",
                "retention_days": 14,
                "time": "01:00",
                "compress": True,
                "options": [
                    "--single-transaction",
                    "--routines",
                    "--triggers",
                ],
            },
        }
        config.add_database(name, db_config)
        console.print(f"  [green]\u2713[/] Added [cyan]{name}[/]")
        added += 1

    return added


def _step_claude_dirs(non_interactive: bool) -> int:
    """Detect and configure Claude Code directories. Returns count found."""
    console.print("\n[bold cyan]Step 4: Claude Code Directories[/]")

    dirs = detect_claude_dirs()

    if not dirs:
        console.print("  No .claude directories found.")
        return 0

    for d in dirs:
        console.print(f"  [green]\u2713[/] Found [cyan]{d}[/]")

    if len(dirs) > 1:
        dir_strings = [str(d) for d in dirs]
        _update_setting("claude_config.claude_dirs", dir_strings)
        console.print(f"  Configured {len(dirs)} Claude directories in settings.")
    else:
        console.print("  Single directory, using default.")

    return len(dirs)


@click.command("init")
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompting (for CI/scripts)")
def init(non_interactive: bool) -> None:
    """Interactive guided setup for Quartermaster.

    Discovers projects, databases, and Claude directories on your machine
    and writes the configuration files.
    """
    console.print(Panel("Quartermaster - Guided Setup", style="bold blue"))

    # Ensure config files exist
    console.print("\n[bold]Checking configuration...[/]")
    if not _ensure_configs():
        console.print("[red]Cannot proceed without config files.[/]")
        raise SystemExit(1)

    # Detect environment
    env = detect_environment()
    if env["is_wsl"]:
        console.print(f"  [green]\u2713[/] WSL detected (Windows user: [cyan]{env.get('win_user', 'unknown')}[/])")
    else:
        console.print(f"  [green]\u2713[/] Linux detected")

    # Run setup steps
    _step_storage(non_interactive)
    projects_added = _step_projects(non_interactive)
    dbs_added = _step_databases(non_interactive)
    claude_count = _step_claude_dirs(non_interactive)

    # Summary
    console.print(Panel("Setup complete!", style="bold green"))
    console.print(f"  Projects configured: [cyan]{projects_added}[/]")
    console.print(f"  Databases configured: [cyan]{dbs_added}[/]")
    console.print(f"  Claude directories: [cyan]{claude_count}[/]")

    console.print("\n  Next steps:")
    console.print("    [yellow]./run.sh[/]          Start the dashboard")
    console.print("    [yellow]./run.sh status[/]   Check status via CLI")
    if projects_added == 0:
        console.print("    [yellow]nano config/projects.yaml[/]   Add projects manually")
