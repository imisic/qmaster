# Quartermaster Setup

Walk through first-time setup of Quartermaster on this machine. Follow these steps in order, checking each before proceeding.

## 1. Check prerequisites

Run these checks and report what's available:
- Python 3.10+ (`python3 --version`)
- git (`git --version`)
- mysqldump (`command -v mysqldump`)

If Python is missing, stop and tell the user to install it.

## 2. Virtual environment

Check if the venv exists at `$HOME/venvs/qmaster` (preferred on WSL) or `./venv`.

If neither exists, run `./setup.sh --non-interactive` to create the venv and install dependencies. If the project is on `/mnt/c/` or `/mnt/d/` (WSL Windows mount), make sure the venv goes to the Linux filesystem at `$HOME/venvs/qmaster`.

Verify the venv works: `$VENV_PATH/bin/python -c "import streamlit, click, yaml"`.

## 3. Configuration

Check if `config/settings.yaml`, `config/projects.yaml`, and `config/databases.yaml` exist.

If any are missing, copy from `.example` files.

Then run the interactive init command:
```
./run.sh init
```

This discovers projects, databases, and Claude directories automatically. Help the user through the prompts.

## 4. Desktop shortcut (WSL only)

If on WSL, check if `Quartermaster.bat` exists on the Windows Desktop. If not, offer to create it:

```bat
@echo off
title Quartermaster
wsl -e bash -c "cd /mnt/c/Users/imisic/qmaster && ./run.sh"
```

## 5. Verify

Run `./run.sh status` to confirm everything loads. Report any errors.

If all good, tell the user they can start the dashboard with `./run.sh` or the desktop shortcut.
