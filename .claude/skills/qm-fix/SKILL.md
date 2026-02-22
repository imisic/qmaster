---
name: qm-fix
description: Debug and fix bugs in Quartermaster. Single mode traces through config/core/utils/web layers to find root cause. Batch mode processes review reports by severity. Use when fixing bugs, debugging issues, processing review findings, or when the user says "fix", "debug", "broken", "not working", "error", or pastes a traceback.
user_invocable: true
---

# Debug and Fix

Fix bugs and review issues while maintaining project patterns.

## Input

$ARGUMENTS

## Mode Detection

- If arguments contain severity markers (Critical, High, Medium, Low) or table format with file:line references: **Batch Mode**
- Otherwise: **Single Bug Mode**

---

## Single Bug Mode

### Step 1: Triage

Understand the symptom before touching code:
- What should happen vs. what actually happens?
- When and where does it occur? (CLI, web, background task?)
- Ask clarifying questions if the description is ambiguous. Don't guess.

### Step 2: Trace the Code Path

Follow the request through the architecture:

```
Config (config/*.yaml via ConfigManager)
    |
Core (backup_engine.py / git_manager.py / config_manager.py)
    |
Utils (parsers, analyzers, schedulers, notifications)
    |
CLI (cli.py Click commands)  /  Web (app.py -> views/ -> components/)
```

Read the relevant files along this path. Start from the entry point (CLI command or web view) and follow the chain.

### Step 3: Diagnose

Identify the root cause, not just the symptom. Check neighboring code for the same pattern. If the bug exists in one place, it likely exists in similar code nearby.

### Step 4: Fix

Apply the fix following project patterns:

**BackupEngine:** Methods return `(bool, str)` tuples. Failures return `(False, message)`, never raise unhandled exceptions. Clean up partial files on failure (temp tar archives, mysql config files). Always create metadata JSON with SHA256 checksum.

**ConfigManager:** All config access through `get_setting()`, `get_storage_paths()`, `get_projects()`, etc. Passwords encrypted with `enc:` prefix. Never read YAML directly outside ConfigManager.

**Web views:** `render_*` functions. After mutations: call `invalidate()` then `st.rerun()`. Error states need `st.error()`. Long operations need `st.spinner()`. Use `st.session_state` for state that persists across reruns.

**Web components:** Reusable UI in `web/components/`. Check existing components before creating new ones: `action_bar`, `backup_card`, `data_table`, `empty_state`, `metrics`, `status_badge`.

**CLI:** Click commands with `@cli.command()`. Rich console output. Proper help text and argument validation.

**Subprocess calls:** Always include `timeout` parameter. Never use `shell=True` with user input. Check return codes.

### Step 5: Verify

Sanity check the changed code:
- Types are correct
- Error paths return properly (BackupEngine: `(False, msg)`, web: `st.error()`)
- No new security issues introduced
- Patterns followed

### Common Bug Patterns

**Cache stale after action**
Cause: Mutation didn't call `invalidate()` before `st.rerun()`.
Fix: Add `invalidate()` call from `web/cache.py` before any `st.rerun()`.

**Backup fails silently**
Cause: Method swallowed exception instead of returning `(False, message)`.
Fix: Wrap in try/except, return `(False, str(e))`, log the error.

**Config key missing / KeyError**
Cause: Accessed dict directly instead of `.get()` with default.
Fix: Use `.get('key', default)` on config/metadata dicts.

**Streamlit reruns break state**
Cause: Used regular variable instead of `st.session_state`.
Fix: Move state to `st.session_state['key']` with initialization check.

**Path errors (str vs Path)**
Cause: Mixed `str` and `Path` objects, or missing `Path()` conversion.
Fix: Be consistent. Core uses `Path` objects, convert at boundaries.

**Web view not showing up**
Cause: View not registered in `PAGE_MAP` (`web/views/__init__.py`) or `ALL_PAGES` (`web/app.py`).
Fix: Add import and entry to both locations.

**Subprocess hangs**
Cause: Missing `timeout` parameter on `subprocess.run()`.
Fix: Add `timeout=seconds` (30s for git, 300s for mysqldump, 600s for tar).

**TypeError on config values**
Cause: YAML returns unexpected types (int vs str, None vs empty string).
Fix: Type check/convert after `get_setting()`, use defaults.

### After Fixing

1. Explain the root cause (not just what you changed)
2. Explain why the fix works
3. Note if similar issues might exist elsewhere

---

## Batch Mode

For processing review reports from `/qm-review`.

### Step 1: Parse Report

Extract all issues. For each, capture:
- File path and line number
- Severity (Critical / High / Medium / Low)
- Description
- Suggested fix (if provided)

### Step 2: Read Affected Files

Read ALL affected files in parallel before making any changes. Understand context first.

### Step 3: Fix in Priority Order

**Critical** — Fix immediately. One at a time. Show the diff before applying. These are security or data-loss risks.

**High** — Fix next. Batch similar fixes together (e.g., all missing type hints, all bare excepts).

**Medium** — Fix if the change is straightforward and low-risk. Skip if it touches complex logic.

**Low** — Fix only if already touching the file for a higher-priority issue.

### Step 4: Report Results

For each issue:
```
Fixed:      [file:line] - [brief description of what was done]
Skipped:    [file:line] - [reason]
Need input: [file:line] - [question for the user]
```

### Gating Rules

- **BLOCK** on critical fixes. Always show the proposed change and ask before applying.
- **BLOCK** on items marked "requires confirmation" in the review report.
- **SKIP** if fix instructions are unclear. Ask for clarification.
- After fixing, run a sanity check on each changed file.
- Maximum 2 fix-review cycles to prevent loops.

---

## Fix Checklist (Both Modes)

Before marking any fix as done:
- [ ] Root cause identified (not just symptom patched)
- [ ] Fix addresses root cause
- [ ] No new security issues introduced
- [ ] Error handling follows project pattern (`(bool, str)` for core, `st.error()` for web)
- [ ] No hardcoded paths (use ConfigManager)
- [ ] No `subprocess` without timeout
- [ ] No duplicate code (checked for existing helpers first)
- [ ] Imports are clean (no unused, no missing)

## Workflow Chain

After fixing, suggest `/qm-review --changed` to verify the fixes, then `/qm-commit` to commit.
