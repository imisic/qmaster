#!/bin/bash

# Setup aliases for Quartermaster
# Automatically detects the install directory

QM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "
# Quartermaster aliases
alias qm=\"cd ${QM_DIR} && ./run.sh\"
alias qm-web=\"cd ${QM_DIR} && ./run.sh\"
alias qm-backup=\"cd ${QM_DIR} && ./run.sh backup\"
alias qm-status=\"cd ${QM_DIR} && ./run.sh status\"
alias qm-projects=\"cd ${QM_DIR} && ./run.sh list-projects\"
alias qm-databases=\"cd ${QM_DIR} && ./run.sh list-databases\"
" >> ~/.bashrc

echo "Aliases added to ~/.bashrc (using ${QM_DIR})"
echo ""
echo "Available commands:"
echo "  qm           - Launch web interface"
echo "  qm-web       - Launch web interface"
echo "  qm-backup    - Backup commands (--all or --project NAME)"
echo "  qm-status    - Show backup status"
echo "  qm-projects  - List configured projects"
echo "  qm-databases - List configured databases"
echo ""
echo "Run 'source ~/.bashrc' to activate aliases"
