#!/bin/bash

# Quartermaster - Startup Script

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment
source "$DIR/venv/bin/activate"

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Quartermaster - Starting...                ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Check if first argument is provided
if [ $# -eq 0 ]; then
    # No arguments, start web interface
    echo -e "${GREEN}Starting web interface...${NC}"
    echo -e "${YELLOW}Open your browser at: http://localhost:8501${NC}"
    echo ""
    streamlit run "$DIR/src/web/app.py"
else
    # Pass arguments to CLI
    python "$DIR/src/cli.py" "$@"
fi
