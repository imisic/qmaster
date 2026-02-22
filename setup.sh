#!/bin/bash

# Quartermaster - First-Time Setup
# Copies example configs, creates venv, and validates prerequisites.

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Quartermaster - Setup                      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
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

if [ ! -d "$DIR/venv" ]; then
    echo -e "${BLUE}Creating virtual environment...${NC}"
    python3 -m venv "$DIR/venv"
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
else
    echo -e "  ${GREEN}✓${NC} Virtual environment exists"
fi

echo -e "${BLUE}Installing dependencies...${NC}"
source "$DIR/venv/bin/activate"
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

# ── Done ─────────────────────────────────────────────────────────

echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Setup complete!                             ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. Edit ${YELLOW}config/settings.yaml${NC} to set your backup storage path"
echo -e "  2. Edit ${YELLOW}config/projects.yaml${NC} to add your projects"
echo -e "  3. Edit ${YELLOW}config/databases.yaml${NC} to add your databases"
echo -e "  4. Run ${YELLOW}./run.sh${NC} to start the web dashboard"
echo -e "     or  ${YELLOW}./run.sh status${NC} to check status via CLI"
echo ""
