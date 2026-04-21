# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quartermaster is a developer toolkit combining backup management, log analysis, code utilities, and productivity tools — all accessible through CLI and web interfaces (Streamlit). Core features include project/database backup with sync support, Apache/PHP log parsing, Claude Code config management, HTML cleaning, and more as needs arise.

## Commands

### Development
```bash
# Activate virtual environment (required before running commands)
source venv/bin/activate

# Run web interface (Streamlit dashboard on http://localhost:8501)
./run.sh
# or: streamlit run src/web/app.py

# Run CLI commands
./run.sh <command>           # shorthand
python src/cli.py <command>  # direct

# Install dependencies
pip install -r requirements.txt
```

### Common CLI Commands
```bash
# Backup operations
./run.sh backup --all                    # Backup all projects
./run.sh backup --project <name>         # Backup specific project
./run.sh backup --project <name> -i      # Incremental backup
./run.sh backup-db --all                 # Backup all databases
./run.sh backup-db --database <name>     # Backup specific database

# Quick snapshot (git + project + databases)
./run.sh snapshot <project> -m "message"

# Restore operations
./run.sh restore <project> <backup_file>
./run.sh restore-db <database> <backup_file>
./run.sh restore-files <project> <backup> "*.py" --target /path

# Status and listing
./run.sh status                          # Show backup status
./run.sh list-projects
./run.sh list-databases

# Verification and tagging
./run.sh verify --project <name> --all   # Verify backup checksums
./run.sh tag --project <name> <backup> --tags production --pin

# Storage and cleanup
./run.sh storage --detailed --cleanup
./run.sh retention --status
./run.sh cleanup --dry-run

# Log viewing
./run.sh apache-logs --lines 100 --severity error
./run.sh php-logs --project <name> --summary
```

### Testing
```bash
pytest                    # Run all tests
pytest -v                 # Verbose output
```

## Architecture

### Core Layer (`src/core/`)
- **config_manager.py**: Loads YAML configs, handles password encryption with Fernet, auto-discovers projects
- **backup_engine.py**: Main orchestrator - creates tar.gz archives, mysqldump backups, handles checksums, retention, incremental backups
- **git_manager.py**: Git integration for savepoints, commits, and repository status

### Interface Layer
- **cli.py**: Click-based CLI with rich console output
- **web/app.py**: Streamlit dashboard with all management features

### Utilities (`src/utils/`)
- **scheduler.py**: Cron-based backup scheduling
- **log_parser.py**: Apache log parsing and analysis
- **php_log_parser.py**: PHP error log parsing
- **storage_analyzer.py**: Disk usage analysis and cleanup recommendations
- **retention_manager.py**: Tiered retention (hourly/daily/weekly/monthly)
- **background_backup.py**: Async backup task management
- **claude_config_manager.py**: Claude Code config cleanup utilities
- **html_cleaner.py**: HTML tag cleaning and sanitization
- **web_scraper.py**: Web page scraping utilities
- **notifications.py**: Notification system

### Configuration (`config/`)
- **projects.yaml**: Project definitions with paths, types, exclusions, git settings
- **databases.yaml**: Database connections with encrypted passwords
- **settings.yaml**: Global settings, storage paths, defaults

### Storage Structure
```
~/backups/qm/                                 # Local backup storage (configurable)
├── projects/<name>/
│   ├── <name>_YYYYMMDD_HHMMSS_full.tar.gz   # Full backup
│   ├── <name>_YYYYMMDD_HHMMSS_incr.tar.gz   # Incremental backup
│   ├── <name>_YYYYMMDD_HHMMSS.json          # Metadata with checksum
│   └── latest.tar.gz                         # Symlink to latest
├── databases/<name>/
│   ├── <name>_YYYYMMDD_HHMMSS.sql.gz
│   └── <name>_YYYYMMDD_HHMMSS.json
└── logs/backup.log

# Optional: secondary sync location (configured in settings.yaml)
```

## Key Patterns

### Password Encryption
Database passwords in `config/databases.yaml` are encrypted with `enc:` prefix. ConfigManager auto-encrypts plain passwords on first load.

### Backup Metadata
Every backup has a companion `.json` file containing:
- SHA256 checksum (mandatory)
- Timestamps, size, backup type
- Tags, importance, pinned status for retention

### Exclusion Patterns
- Folders starting with `_` or `.` are always excluded
- Project-specific exclusions in `projects.yaml` (vendor/, node_modules/, etc.)

### Incremental Backups
Uses snapshot JSON to track file mtimes. Falls back to full backup if no previous full exists.

### Smart Copy
Secondary sync uses checksum comparison to skip unchanged files.

## Web Dashboard Pages

Sidebar nav is defined in `src/web/app.py::SIDEBAR_NAV`. Views live in `src/web/views/` and must be registered in `src/web/views/__init__.py::PAGE_MAP`.

**Backups section:**
- **Dashboard**: Overview, quick actions, analytics charts, recent backups, timeline
- **Projects**: Per-project management, git history, backup/restore, file preview
- **Databases**: Database backup/restore management
- **Storage & Retention**: Storage analytics, age-based cleanup for local and sync storage, retention tiers

**Utilities section:**
- **Claude Cleanup**: Claude Code `.claude` directory scanning and cleanup (Sessions / Config Dump / Project History / MCP Servers tabs)
- **Logs**: Apache and PHP log viewer with search, severity filtering, stats, export
- **Tools**: HTML Cleaner, Web Scraper, and Text Sanitizer as tabs under one page

## Coding Rules (`.claude/rules/`)

Path-scoped rules auto-loaded when editing matching files. These encode the same standards the preflight scripts check for, so issues get prevented rather than caught after the fact.

| Rule file | Scope | Covers |
|-|-|-|
| `security.md` | `src/**/*.py` | Subprocess safety, input validation, secrets, XSS, path traversal, SSRF, rate limiting |
| `web-layer.md` | `src/web/**/*.py` | Cache invalidation, rerun discipline, view registration, no print() |
| `error-handling.md` | `src/**/*.py` | Exception specificity, silent failures, logging, resilience/retry |
| `code-quality.md` | `src/**/*.py` | Size limits, modern types, safe access, dead code, performance, logging format |
| `file-operations.md` | `src/core/**/*.py`, `src/utils/**/*.py` | Atomic writes, TOCTOU prevention, file locking |

## Skills (`.claude/skills/`)

Project-specific Claude Code skills. Invoke via `/qm-<name>`:

| Skill | Purpose |
|-|-|
| `/qm-commit` | Git commit with pre-flight checks, sensitive file scanning, conventional commits |
| `/qm-review` | Code review with 3 parallel agents (Security, Architecture, Quality) + preflight scripts |
| `/qm-fix` | Debug and fix bugs (single mode) or process review reports (batch mode) |
| `/qm-ship` | Full pipeline: review -> fix -> commit |

## Preflight Scripts (`.claude/scripts/`)

Automated checks that output JSON with pass/warn/fail status:

- **preflight-review.sh**: Type hints coverage, print statements, hardcoded paths, bare excepts, subprocess safety, secrets detection
- **preflight-perf.sh**: N+1 candidates, cache usage, missing invalidation, large files/functions, deep nesting, duplicates
- **preflight-quality.sh**: Config compliance, view registration, component exports, commented code, magic numbers, test coverage

Run manually: `bash .claude/scripts/preflight-review.sh`
