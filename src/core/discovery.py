"""
Environment detection, project/database discovery, and Claude directory scanning.

Used by setup.sh (via CLI), `qm init`, and Claude cleanup multi-dir support.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Marker files that identify project types
_TYPE_MARKERS: dict[str, str] = {
    "composer.json": "php",
    "package.json": "node",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "Gemfile": "ruby",
}

# Directories to skip during project scanning (case-sensitive)
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    ".git", ".hg", ".svn", "dist", "build", ".next",
    ".cache", ".tox", "target", "bin", "obj",
    "backups", ".claude",
})

# MySQL system databases to exclude from discovery
_SYSTEM_DBS: frozenset[str] = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "phpmyadmin",
})

# Type-appropriate exclude patterns for projects.yaml
_TYPE_EXCLUDES: dict[str, list[str]] = {
    "php": [
        "vendor/", "node_modules/", "storage/cache/", "storage/logs/",
        "storage/sessions/", "_debug/", ".git/",
    ],
    "node": [
        "node_modules/", "dist/", "build/", ".next/", ".git/",
    ],
    "python": [
        "venv/", ".venv/", "__pycache__/", "*.pyc", ".pytest_cache/",
        ".env", ".git/", "*.egg-info/", "dist/", "build/",
    ],
    "rust": [
        "target/", ".git/",
    ],
    "go": [
        "vendor/", ".git/",
    ],
    "ruby": [
        "vendor/", ".git/",
    ],
}


_cached_env: dict[str, Any] | None = None


def detect_environment() -> dict[str, Any]:
    """Detect OS environment, WSL status, and Windows user info.

    Result is cached for the process lifetime since these values don't change.

    Returns:
        Dict with keys: is_wsl, win_user, win_home, linux_home
    """
    global _cached_env
    if _cached_env is not None:
        return _cached_env

    result: dict[str, Any] = {
        "is_wsl": False,
        "win_user": None,
        "win_home": None,
        "linux_home": Path.home(),
    }

    try:
        proc_version = Path("/proc/version")
        if proc_version.exists() and "microsoft" in proc_version.read_text().lower():
            result["is_wsl"] = True
    except OSError:
        pass

    if result["is_wsl"]:
        result["win_user"] = _detect_windows_user()
        if result["win_user"]:
            win_home = Path(f"/mnt/c/Users/{result['win_user']}")
            if win_home.is_dir():
                result["win_home"] = win_home

    _cached_env = result
    return result


def _detect_windows_user() -> str | None:
    """Detect the Windows username from WSL."""
    # Try wslvar first (most reliable)
    try:
        out = subprocess.run(
            ["wslvar", "USERNAME"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to scanning /mnt/c/Users/ for non-system directories
    users_dir = Path("/mnt/c/Users")
    if users_dir.is_dir():
        skip = {"Public", "Default", "Default User", "All Users", "desktop.ini"}
        candidates = [
            d.name for d in users_dir.iterdir()
            if d.is_dir() and d.name not in skip and not d.name.startswith(".")
        ]
        if len(candidates) == 1:
            return candidates[0]
        # If multiple, try matching Linux username
        linux_user = os.environ.get("USER", "")
        for c in candidates:
            if c.lower() == linux_user.lower():
                return c

    return None


def get_scan_roots(env: dict[str, Any] | None = None) -> list[Path]:
    """Return candidate directories to scan for projects.

    Args:
        env: Environment dict from detect_environment(). Auto-detected if None.
    """
    if env is None:
        env = detect_environment()

    home = env["linux_home"]
    roots: list[Path] = []

    # Linux home subdirs
    for name in ("projects", "repos", "code", "src", "dev", "my_projects"):
        candidate = home / name
        if candidate.is_dir():
            roots.append(candidate)

    # WSL: Windows user directories
    win_home = env.get("win_home")
    if win_home and win_home.is_dir():
        for name in ("projects", "repos", "code", "src", "dev", "my_projects"):
            candidate = win_home / name
            if candidate.is_dir():
                roots.append(candidate)
        # Also scan direct children of Windows home (people put projects there)
        roots.append(win_home)

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        resolved = r.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(r)

    return unique


def scan_for_projects(roots: list[Path], max_depth: int = 3) -> list[dict[str, Any]]:
    """Scan directories for projects (identified by .git presence).

    Args:
        roots: Directories to scan
        max_depth: How deep to search (default 3)

    Returns:
        List of dicts: {name, path, type, has_git}
    """
    projects: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for root in roots:
        if not root.is_dir():
            continue
        _scan_dir(root, 0, max_depth, projects, seen_paths)

    # Sort by name
    projects.sort(key=lambda p: p["name"].lower())
    return projects


def _scan_dir(
    directory: Path,
    depth: int,
    max_depth: int,
    results: list[dict[str, Any]],
    seen: set[str],
) -> None:
    """Recursively scan a directory for projects."""
    if depth > max_depth:
        return

    try:
        entries = list(os.scandir(directory))
    except (PermissionError, OSError):
        return

    has_git = False
    subdirs: list[os.DirEntry] = []

    for entry in entries:
        if entry.name == ".git" and entry.is_dir(follow_symlinks=False):
            has_git = True
        elif entry.is_dir(follow_symlinks=False):
            subdirs.append(entry)

    if has_git:
        path_str = str(directory)
        if path_str not in seen:
            seen.add(path_str)
            project_type = _detect_project_type(entries)
            results.append({
                "name": directory.name,
                "path": path_str,
                "type": project_type,
                "has_git": True,
            })
        return

    for entry in subdirs:
        if entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue
        _scan_dir(Path(entry.path), depth + 1, max_depth, results, seen)


def _detect_project_type(entries: list[os.DirEntry]) -> str:
    """Detect project type from directory entries using marker files."""
    entry_names = {e.name for e in entries if e.is_file(follow_symlinks=False)}

    for marker, project_type in _TYPE_MARKERS.items():
        if marker in entry_names:
            return project_type

    return "generic"


def build_project_config(project: dict[str, Any]) -> dict[str, Any]:
    """Build a full projects.yaml config entry from a discovered project.

    Args:
        project: Dict from scan_for_projects ({name, path, type, has_git})

    Returns:
        Config dict matching projects.yaml structure
    """
    project_type = project.get("type", "generic")
    excludes = _TYPE_EXCLUDES.get(project_type, [".git/"])

    config: dict[str, Any] = {
        "path": project["path"],
        "type": project_type,
        "description": project["name"],
        "backup": {
            "enabled": True,
            "schedule": "daily",
            "retention_days": 30,
            "time": "02:00",
        },
        "git": {
            "track": project.get("has_git", False),
            "auto_commit": False,
            "branch": "main",
        },
        "exclude": list(excludes),
    }

    return config


def scan_for_databases(user: str = "root", host: str = "localhost", port: int = 3306) -> list[dict[str, Any]]:
    """Check if MySQL/MariaDB is running and list available databases.

    Args:
        user: MySQL user to connect as
        host: MySQL host
        port: MySQL port

    Returns:
        List of dicts: {name, type, host, port}
    """
    # Check if mysqld is running
    try:
        result = subprocess.run(
            ["pgrep", "-x", "mysqld"],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            # Also check mariadbd
            result = subprocess.run(
                ["pgrep", "-x", "mariadbd"],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                return []
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("Process check unavailable: %s", e)
        return []

    # List databases
    try:
        result = subprocess.run(
            ["mysql", "-u", user, "-h", host, "-P", str(port),
             "--batch", "--skip-column-names", "-e", "SHOW DATABASES"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.warning("MySQL connection failed: %s", result.stderr.strip())
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Could not query MySQL: %s", e)
        return []

    databases = []
    for line in result.stdout.strip().splitlines():
        db_name = line.strip()
        if db_name and db_name not in _SYSTEM_DBS:
            databases.append({
                "name": db_name,
                "type": "mysql",
                "host": host,
                "port": port,
            })

    return databases


def detect_claude_dirs(env: dict[str, Any] | None = None) -> list[Path]:
    """Find all .claude directories on this machine.

    Args:
        env: Environment dict from detect_environment(). Auto-detected if None.

    Returns:
        List of existing .claude directory paths
    """
    if env is None:
        env = detect_environment()

    candidates: list[Path] = [env["linux_home"] / ".claude"]

    win_home = env.get("win_home")
    if win_home:
        candidates.append(win_home / ".claude")

    return [p for p in candidates if p.is_dir()]
