#!/bin/bash

# Quartermaster - First-Time Setup
# Copies example configs, creates venv, validates prerequisites, and offers guided config.

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse flags ─────────────────────────────────────────────────
NON_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=true ;;
    esac
done

# ── Detect environment ──────────────────────────────────────────
IS_WSL=false
WIN_USER=""
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
    # Detect Windows username
    if command -v wslvar &> /dev/null; then
        WIN_USER=$(wslvar USERNAME 2>/dev/null || true)
    fi
    if [ -z "$WIN_USER" ]; then
        # Fall back: find non-system user dirs under /mnt/c/Users
        for d in /mnt/c/Users/*/; do
            uname=$(basename "$d")
            case "$uname" in
                Public|Default|"Default User"|"All Users"|desktop.ini) continue ;;
                *) WIN_USER="$uname"; break ;;
            esac
        done
    fi
fi

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Quartermaster - Setup                      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

if [ "$IS_WSL" = true ]; then
    echo -e "  ${GREEN}✓${NC} WSL detected (Windows user: ${YELLOW}${WIN_USER:-unknown}${NC})"
else
    echo -e "  ${GREEN}✓${NC} Linux detected"
fi
echo ""

# ── Check Prerequisites ──────────────────────────────────────────

echo -e "${BLUE}Checking prerequisites...${NC}"

# Python 3.10+
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        echo -e "  ${GREEN}✓${NC} Python $PY_VERSION"
    else
        echo -e "  ${RED}✗${NC} Python $PY_VERSION (need 3.10+)"
        exit 1
    fi
else
    echo -e "  ${RED}✗${NC} Python 3 not found"
    exit 1
fi

# Git
if command -v git &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} git $(git --version | cut -d' ' -f3)"
else
    echo -e "  ${YELLOW}!${NC} git not found (git features will be unavailable)"
fi

# mysqldump
if command -v mysqldump &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} mysqldump available"
else
    echo -e "  ${YELLOW}!${NC} mysqldump not found (database backups will be unavailable)"
    echo -e "       Install with: sudo apt install mysql-client"
fi

echo ""

# ── Virtual Environment ──────────────────────────────────────────

# On WSL with project on /mnt/, put venv on Linux filesystem for performance
VENV_PATH="$DIR/venv"
if [ "$IS_WSL" = true ] && echo "$DIR" | grep -q "^/mnt/"; then
    VENV_PATH="$HOME/venvs/qmaster"
    echo -e "${BLUE}WSL detected with project on Windows mount.${NC}"
    echo -e "  Venv will be created on the Linux filesystem for speed."
    echo -e "  Location: ${YELLOW}$VENV_PATH${NC}"
    echo ""
fi

if [ ! -d "$VENV_PATH" ]; then
    echo -e "${BLUE}Creating virtual environment...${NC}"
    mkdir -p "$(dirname "$VENV_PATH")"
    python3 -m venv "$VENV_PATH"
    echo -e "  ${GREEN}✓${NC} Virtual environment created at $VENV_PATH"
else
    echo -e "  ${GREEN}✓${NC} Virtual environment exists at $VENV_PATH"
fi

echo -e "${BLUE}Installing dependencies...${NC}"
source "$VENV_PATH/bin/activate"
pip install -q -r "$DIR/requirements.txt"
echo -e "  ${GREEN}✓${NC} Dependencies installed"
echo ""

# ── Configuration Files ──────────────────────────────────────────

echo -e "${BLUE}Setting up configuration...${NC}"

copy_config() {
    local example="$1"
    local target="$2"
    local name="$3"

    if [ -f "$target" ]; then
        echo -e "  ${GREEN}✓${NC} $name already exists (skipping)"
    else
        cp "$example" "$target"
        echo -e "  ${GREEN}✓${NC} $name created from example"
    fi
}

copy_config "$DIR/config/settings.yaml.example" "$DIR/config/settings.yaml" "settings.yaml"
copy_config "$DIR/config/projects.yaml.example" "$DIR/config/projects.yaml" "projects.yaml"
copy_config "$DIR/config/databases.yaml.example" "$DIR/config/databases.yaml" "databases.yaml"

echo ""

# ── Interactive Storage Path ─────────────────────────────────────

if [ "$NON_INTERACTIVE" = false ]; then
    DEFAULT_STORAGE="~/backups/qm"
    echo -e "${BLUE}Configure backup storage path:${NC}"
    read -p "  Storage path [$DEFAULT_STORAGE]: " STORAGE_PATH
    STORAGE_PATH="${STORAGE_PATH:-$DEFAULT_STORAGE}"

    # Use Python to update settings.yaml safely
    "$VENV_PATH/bin/python" -c "
import yaml
from pathlib import Path

settings_file = Path('$DIR/config/settings.yaml')
with open(settings_file) as f:
    settings = yaml.safe_load(f) or {}

if 'storage' not in settings:
    settings['storage'] = {}
settings['storage']['local_base'] = '$STORAGE_PATH'

# Also set claude export path based on storage path
if 'claude_config' not in settings:
    settings['claude_config'] = {}
settings['claude_config']['export_path'] = '$STORAGE_PATH/claude_exports'

with open(settings_file, 'w') as f:
    yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
"
    echo -e "  ${GREEN}✓${NC} Storage path set to ${YELLOW}$STORAGE_PATH${NC}"
    echo ""
fi

# ── Smoke Test ───────────────────────────────────────────────────

echo -e "${BLUE}Verifying install...${NC}"
if "$VENV_PATH/bin/python" -c "import streamlit, click, yaml, cryptography, pandas, plotly" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Core imports OK"
else
    echo -e "  ${RED}✗${NC} Import check failed. Re-run: pip install -r requirements.txt"
    exit 1
fi

# Validate config loads
if cd "$DIR/src" && "$VENV_PATH/bin/python" -c "from core.config_manager import ConfigManager; ConfigManager()" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Configuration loads OK"
else
    echo -e "  ${YELLOW}!${NC} Configuration validation failed. Edit your config files and retry."
fi
cd "$DIR"

echo ""

# ── Shell Aliases ────────────────────────────────────────────────

if [ "$NON_INTERACTIVE" = false ]; then
    read -p "Set up shell aliases (qm, qm-web, etc.)? [y/N]: " SETUP_ALIASES
    if [ "$SETUP_ALIASES" = "y" ] || [ "$SETUP_ALIASES" = "Y" ]; then
        source "$DIR/setup-aliases.sh"
        echo -e "  ${GREEN}✓${NC} Aliases added"
    fi
else
    echo -e "  Skipping alias setup (non-interactive mode)"
fi

echo ""

# ── Windows Desktop Shortcut ─────────────────────────────────────

if [ "$IS_WSL" = true ] && [ -n "$WIN_USER" ] && [ "$NON_INTERACTIVE" = false ]; then
    DESKTOP_PATH="/mnt/c/Users/$WIN_USER/Desktop"
    if [ -d "$DESKTOP_PATH" ]; then
        read -p "Create Windows desktop shortcut for Quartermaster? [y/N]: " CREATE_SHORTCUT
        if [ "$CREATE_SHORTCUT" = "y" ] || [ "$CREATE_SHORTCUT" = "Y" ]; then
            cat > "$DESKTOP_PATH/Quartermaster.bat" << EOFBAT
@echo off
title Quartermaster
wsl -e bash -c "cd $DIR && ./run.sh"
EOFBAT
            echo -e "  ${GREEN}✓${NC} Desktop shortcut created"
        fi
    fi
fi

# ── Done ─────────────────────────────────────────────────────────

echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Setup complete!                             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Run ${YELLOW}./run.sh init${NC} for guided project and database discovery"
echo -e "  2. Or edit configs manually:"
echo -e "     ${YELLOW}config/settings.yaml${NC}   Storage paths, defaults"
echo -e "     ${YELLOW}config/projects.yaml${NC}   Your projects"
echo -e "     ${YELLOW}config/databases.yaml${NC}  Database connections"
echo -e "  3. Run ${YELLOW}./run.sh${NC} to start the dashboard"
echo -e "     or  ${YELLOW}./run.sh status${NC} to check status via CLI"
echo ""
