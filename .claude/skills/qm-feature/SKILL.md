---
name: qm-feature
description: Scaffold new features for Quartermaster across all layers (CLI commands, utilities, web views, web components, core modules). Generates checklists, searches for existing code first, follows project registration patterns. Use when adding new functionality, creating new pages, building new commands, scaffolding modules, or when the user says "add feature", "new page", "new command", "new view", "scaffold", or "create a module".
user_invocable: true
---

# Feature Scaffolding

Scaffold new features across any layer of Quartermaster.

## Input

$ARGUMENTS

## Phase 1: Parse & Plan

Determine what's needed from the description:

| Layer | When | Files Involved |
|-|-|-|
| CLI command | New `./run.sh <command>` | `src/cli.py` |
| Utility module | Shared logic, parsing, analysis | `src/utils/<name>.py` |
| Web view (page) | New Streamlit dashboard page | `src/web/views/<name>.py`, `__init__.py`, `app.py` |
| Web component | Reusable UI element | `src/web/components/<name>.py`, `__init__.py` |
| Core module | Backup/config/git capability | `src/core/<name>.py` |

A feature often spans multiple layers (e.g., a new utility + CLI command + web view). Identify all layers needed upfront.

## Phase 2: Search Existing Code (MANDATORY)

Before writing ANY new code, search the codebase for:

1. **Existing implementations** that already do what's needed (or close to it)
2. **Similar patterns** to follow for consistency
3. **Existing components** that can be reused in the UI
4. **Utility functions** that already exist in `src/utils/`

This is non-negotiable. Duplicating existing code is worse than not having the feature at all. If something similar exists, extend it rather than creating a parallel implementation.

## Phase 3: Generate Checklist

Based on the feature type, generate a checklist. Multiple types can combine.

### New CLI Command

- [ ] Add function in `src/cli.py` with `@cli.command()` decorator
- [ ] Add proper help text via Click's `help=` parameter
- [ ] Add argument/option validation with Click types
- [ ] Use Rich console for user feedback (`console.print()`, `console.status()`)
- [ ] Wire up to core/utils modules (don't put business logic in CLI)
- [ ] Add to `run.sh` help output if applicable
- [ ] Test: `python src/cli.py <command> --help` works

**Reference:** Any existing command in `src/cli.py`

### New Utility Module

- [ ] Create `src/utils/<name>.py`
- [ ] Type hints on ALL public functions (parameters and return)
- [ ] Proper error handling with context (try/except, log what happened)
- [ ] Logging via `logging.getLogger(__name__)`
- [ ] No hardcoded paths (accept paths as parameters or use ConfigManager)
- [ ] Docstring on module and public functions explaining purpose

**Reference:** `src/utils/storage_analyzer.py` (well-structured utility pattern)

### New Web View (Page)

This has the most registration points. Miss one and the page won't show up.

- [ ] Create `src/web/views/<name>.py` with `render_<name>(app)` function
- [ ] **Register in `src/web/views/__init__.py`**: add import AND entry to `PAGE_MAP`
- [ ] **Register in `src/web/app.py`**: add page name to `ALL_PAGES` list
- [ ] Accept `AppComponents` parameter (from `web/state.py` via `init_app_state()`)
- [ ] Use existing components from `web/components/` where applicable
- [ ] Cache expensive operations: add function in `src/web/cache.py` with `@st.cache_data(ttl=N)`
- [ ] After any mutation: call `invalidate()` then `st.rerun()`
- [ ] Error states: `st.error()` for failures
- [ ] Loading states: `st.spinner()` for long operations
- [ ] Use `st.session_state` for any state that needs to persist across reruns
- [ ] Page title via `st.header()` or `st.title()`

**Reference:** `src/web/views/html_cleaner.py` (clean, recent pattern)

### New Web Component

- [ ] Create `src/web/components/<name>.py`
- [ ] **Export in `src/web/components/__init__.py`**
- [ ] Reusable: parameterized, no hardcoded values
- [ ] No side effects (don't mutate state, call APIs, or trigger reruns)
- [ ] Type hints on all parameters

**Reference:** `src/web/components/data_table.py`

### New Core Module

- [ ] Create `src/core/<name>.py`
- [ ] If needed by web: integrate in `AppComponents` dataclass in `web/state.py`
- [ ] If needed by CLI: integrate in `src/cli.py`
- [ ] Error handling: return `(bool, str)` for operations that can fail (following BackupEngine pattern)
- [ ] Config access through ConfigManager only
- [ ] Type hints on all public methods

**Reference:** `src/core/backup_engine.py` (main orchestrator pattern)

## Phase 4: Scaffold

Create files following the checklist. For each file:
1. Follow the reference file's structure and patterns
2. Include all registration points (imports, PAGE_MAP, ALL_PAGES, __init__.py exports)
3. Add type hints from the start
4. Use existing components/utilities rather than writing inline

## Phase 5: Post-Check (MANDATORY)

Verify before declaring the feature complete:

- [ ] All imports resolve (no `ModuleNotFoundError`)
- [ ] Type hints present on all public functions — **BLOCK if missing**
- [ ] No hardcoded paths (use ConfigManager or function parameters)
- [ ] Web views registered in both `PAGE_MAP` and `ALL_PAGES` — **BLOCK if missing**
- [ ] Components exported from `__init__.py` — **BLOCK if missing**
- [ ] Error handling on I/O, subprocess, and external calls
- [ ] No duplicate code (Phase 2 search confirmed)
- [ ] Sensitive data not exposed in error messages

## Reference Files Per Layer

| Layer | Best Reference |
|-|-|
| CLI command | `src/cli.py` (any existing command) |
| Utility | `src/utils/storage_analyzer.py` |
| Web view | `src/web/views/html_cleaner.py` |
| Web component | `src/web/components/data_table.py` |
| Core module | `src/core/backup_engine.py` |
| Cache function | `src/web/cache.py` |
| App state | `src/web/state.py` |
| View registration | `src/web/views/__init__.py` |

## Workflow Chain

After scaffolding, suggest `/qm-review --changed` to verify quality, then `/qm-commit` to commit.
