---
name: qm-review
description: Unified code review and tech debt analysis for Quartermaster. Dispatches 3 parallel sub-agents (Security, Architecture, Quality) with severity-gated reporting. Supports --changed (default), --full, --security-only, --debt flags. Use when reviewing code, checking quality, auditing security, scanning for tech debt, or when the user says "review", "check this", "audit", "tech debt", or "code quality".
user_invocable: true
---

# Unified Code Review

Target: $ARGUMENTS (default: changed files via `git diff --name-only HEAD`).

**Flags:**
- `--changed` (default) — only files changed since last commit
- `--full` — scan all of `src/`
- `--security-only` — Security agent only
- `--debt` — add tech debt scoring (0-100 scale)

## Pre-flight

Run the comprehensive preflight script:

```bash
bash .claude/scripts/preflight-comprehensive.sh
```

Display the JSON summary. Split findings by check ID prefix for agent injection:
- **Security agent gets**: SEC-*, SUB-*
- **Architecture agent gets**: WEB-*, EXC-*, PERF-*
- **Quality agent gets**: TYPE-*, QUAL-*

If the script doesn't exist, report the error and skip preflight (legacy scripts have been consolidated into the comprehensive script).

## Execution: 3 Parallel Sub-Agents

Launch all three simultaneously. Scopes are **strictly non-overlapping**.

| Agent | Owns | Does NOT Check |
|-|-|-|
| **Security** | Command injection, path traversal, credentials, XSS, file permissions, deserialization, SSRF, tempfile safety | Exception handling, cache patterns, type hints |
| **Architecture** | Cache invalidation/rerun, view/component registration, config access, error handling, backup return patterns, resource leaks | Security, type hints |
| **Quality** | Type modernization, annotations, dead code, complexity, print statements, dict access safety | Security, cache patterns |

If `--security-only` flag is set, run only the Security agent.

After all agents complete, **deduplicate**: if the same file:line appears in multiple reports, keep only the highest-severity instance.

## Fix Readiness Requirement

Every finding MUST include all 4 fields:

1. **File:line** — exact location
2. **Current** — what the code says now (quoted)
3. **Should be** — what it should say (quoted)
4. **Why** — one sentence explaining the risk

Findings without all 4 fields are incomplete. Agents must revise before output.

---

## Security Agent

You are reviewing Quartermaster, a Python 3.10+ backup management tool with Streamlit web UI and Click CLI.

### YOUR SCOPE (do not check anything outside this)

1. **Command injection**: subprocess with shell=True, string concatenation in args, unsanitized user input in commands
2. **Path traversal**: tar extraction safety, restore path validation, user-provided paths
3. **Credential exposure**: hardcoded passwords, secrets in logs/error messages, key file permissions
4. **XSS in Streamlit**: unsafe_allow_html=True with unescaped user data in f-strings
5. **File permissions**: backup files, temp files, config files should be restrictive
6. **Unsafe deserialization**: pickle.load(), yaml.load() without SafeLoader, eval() on untrusted data
7. **SSRF**: requests.get/post with user-controlled URLs without allowlist validation
8. **Tempfile safety**: mkstemp/NamedTemporaryFile(delete=False) without cleanup in finally block (owns: whether cleanup exists; Architecture owns: whether cleanup uses context managers)

### KNOWN CORRECT PATTERNS (do not flag these)

- `subprocess.run([...], timeout=N, capture_output=True)` with list args — correct pattern
- `password="{password}"` in `database_ops.py:57` — f-string writing decrypted password to temp MySQL config file, not a hardcoded secret. The password comes from ConfigManager Fernet decryption.
- `st.markdown(f'<div class="status-{level}">...', unsafe_allow_html=True)` where the variable is an internal status string (healthy/warning/critical), not user input — SAFE
- `html.escape()` and `html_mod.escape()` (alias in storage_cleanup.py) wrapping user content — SAFE
- `_safe_extractall()` in project_ops.py validates tar member paths against traversal — SAFE
- `.example` config files are committed to git; real configs are gitignored
- `config/.encryption_key` has 0o600 permissions enforced in config_manager.py:59-65
- `web_scraper.py` has `_is_safe_url()` validation before `requests.get()` — SAFE, not SSRF
- `database_ops.py:41` uses `tempfile.mkstemp()` for MySQL config — cleaned up in finally block
- `scheduler.py:35` uses `NamedTemporaryFile(delete=False)` — cleaned up with `os.unlink()` after use

### WHAT TO LOOK FOR

For each `unsafe_allow_html=True` usage (preflight SEC-04 reports locations):
- Trace each interpolated variable to its source
- If source is: internal status string, CSS class, numeric value → SAFE, skip
- If source is: user input (text_area, text_input, URL, file upload, config value) → FLAG as XSS risk
- If source is: function parameter where callers might pass user data → FLAG with note about caller chain

For subprocess calls:
- Verify all have `timeout=` (preflight SUB-01/02 catches most, but verify edge cases)
- Verify no string interpolation in command arguments
- Check `Popen` calls have `.wait(timeout=)` or `.communicate(timeout=)`

### Added by review-optimizer [2026-03-23]

For deserialization (preflight SEC-08 reports locations):
- Any `pickle.load()` or `pickle.loads()` on data from outside the process → CRITICAL
- Any `yaml.load()` without `Loader=SafeLoader` → HIGH
- Any `eval()` that isn't `ast.literal_eval()` → CRITICAL

For SSRF (preflight SEC-09 reports locations):
- Trace each `requests.get/post()` URL to its source
- If URL comes from user input (text_input, URL parameter, config) without validation → FLAG
- If URL passes through `_is_safe_url()` or similar allowlist → SAFE

For tempfile safety (preflight SEC-10 reports locations):
- `mkstemp()` and `NamedTemporaryFile(delete=False)` must have cleanup in a `finally` block
- Check: is `os.unlink(path)` guaranteed to run even if the function raises?

### PREFLIGHT KNOWN ISSUES (verify each one)

{Inject SEC-* and SUB-* findings from preflight JSON here}

For each preflight finding: confirm it exists, or mark as false-positive with one-sentence reasoning.

### OUTPUT FORMAT

For each finding:
```
[SEVERITY] file:line
  Current: `code as-is`
  Should be: `fixed code`
  Why: one sentence
```

Final response under 3000 characters. Group by severity. Skip INFO items unless fewer than 3 CRITICAL+HIGH.

---

## Architecture Agent

You are reviewing Quartermaster, a Python 3.10+ backup management tool with Streamlit web UI.

### YOUR SCOPE (do not check anything outside this)

1. **Cache invalidation**: every `st.rerun()` after data mutation must have `invalidate()` within 10 preceding lines
2. **Import requirements**: views using `st.rerun()` must import `invalidate` from `web.cache`
3. **View registration**: all `render_*` functions must be in `PAGE_MAP` (`web/views/__init__.py`)
4. **Component exports**: component files must have exports in `web/components/__init__.py`
5. **Config access**: all settings through ConfigManager methods, no direct YAML reads
6. **Error handling**: no bare except, no silent exception swallowing, proper logging in catch blocks
7. **Backup return pattern**: backup/restore methods must return `tuple[bool, str]`
8. **Cache TTL**: `@st.cache_data` decorators should have `ttl=` to prevent stale data (preflight WEB-06)
9. **Resource cleanup**: file handles and subprocess Popen objects must use context managers or try/finally (owns: cleanup *mechanism*; Security owns: whether tempfile cleanup *exists*)

### THE invalidate() RULE

`web/cache.py` defines `invalidate()` which calls `st.cache_data.clear()`. After ANY data mutation (backup, restore, cleanup, delete, add project/database, clear log, config change), views must call `invalidate()` then `st.rerun()`.

**DOES need invalidate():**
- After backup/restore operations
- After cleanup/delete operations
- After config changes
- After log clear operations
- After any operation that changes data on disk

**Does NOT need invalidate() (UI-only state changes):**
- Cancel/Close/Dismiss button handlers (only clear session_state)
- Select All / Deselect All (only toggle session_state checkboxes)
- Top N selection (only toggle session_state checkboxes)
- Tab/mode switching (only change session_state display flag)

### KNOWN CORRECT PATTERNS (do not flag)

- `src/web/views/databases.py:102` — `invalidate()` then `st.rerun()` after backup. CORRECT.
- `src/web/views/storage_cleanup.py:183,223,236` — `invalidate()` before `st.rerun()` after cleanup. CORRECT.
- `src/web/views/storage_cleanup.py:426,431,439` — `st.rerun()` after session_state-only changes (Select All/Deselect). No invalidate needed. CORRECT.
- `src/web/views/projects.py:234` — `st.rerun()` on Cancel. No invalidate needed. CORRECT.
- `except (OSError, PermissionError): pass` after `unlink()`/`remove()` — cleanup context, acceptable.
- `except Exception` in `src/core/git_manager.py` (14 instances, lines 79-386) — git operations fail unpredictably; these return `(bool, str)` tuples, which is the correct pattern for this module. Do not flag.
- `BackupEngine` methods return `(bool, str)` tuples — success `(True, msg)` or failure `(False, msg)`.
- Web state initialized once via `@st.cache_resource` in `web/state.py` — don't flag as missing cache.

### WHAT TO LOOK FOR

For each `st.rerun()` call (preflight WEB-01 reports locations):
- Check if it follows a data mutation → needs invalidate()
- Check if it follows only session_state changes → OK without invalidate()
- If WEB-01 flags it and it's after a real mutation, confirm as FAIL

For exception handlers (preflight EXC-03 reports silent ones):
- Verify each silent handler: is the except body just `pass`/`continue` with no logging?
- Is this a cleanup context (after unlink/remove)? → OK
- Otherwise → should at minimum log at debug level

### Added by review-optimizer [2026-03-23]

For `@st.cache_data` without TTL (preflight WEB-06 reports locations):
- Data that changes on user action (backup status, log entries) needs TTL or explicit invalidation
- Static data (CSS, theme) is OK without TTL

For resource cleanup (preflight PERF-01/02 reports locations):
- File I/O inside loops (e.g., `json.load()` per backup in storage_analyzer.py) — check if batching is possible
- Subprocess inside loops (PERF-02) — check if the loop can be replaced with a single command
- `subprocess.Popen` without context manager — must have `.wait()` or `.communicate()` with timeout

For `except Exception` blocks (preflight EXC-04 reports locations):
- `src/core/git_manager.py` uses broad `except Exception` deliberately — git operations fail unpredictably, and each method returns `(bool, str)`. Skip these.
- Other files: verify the handler logs the exception or returns a meaningful error. Silent broad catches outside cleanup/git contexts are a code smell.

### PREFLIGHT KNOWN ISSUES (verify each one)

{Inject WEB-*, EXC-*, and PERF-* findings from preflight JSON here}

### OUTPUT FORMAT

For each finding:
```
[SEVERITY] file:line
  Current: `code as-is`
  Should be: `fixed code`
  Why: one sentence
```

Final response under 3000 characters. Group by severity.

---

## Quality Agent

You are reviewing Quartermaster, a Python 3.10+ backup management tool.

### YOUR SCOPE (do not check anything outside this)

1. **Type modernization**: `list` not `List`, `X | None` not `Optional[X]`, builtins not `typing.Dict`
2. **Public function annotations**: params + return types on public functions
3. **Dead code**: empty files, unused imports, commented-out code blocks >3 lines
4. **Complexity**: files >300 lines, functions >50 lines, nesting >4 levels
5. **print() usage**: should use `logger` or `console.print()`
6. **Unsafe nested dict access**: `dict["a"]["b"]` on external data without `.get()` guard

### KNOWN CORRECT PATTERNS (do not flag)

- `from typing import Any` — `Any` has no builtin equivalent, still needed in 3.10+
- `from typing import cast` — still needed for runtime type narrowing
- `from typing import TYPE_CHECKING` — still needed for conditional imports
- Click `@cli.command()` decorated functions — called by Click framework, not dead code
- `render_*` functions in views — called via `PAGE_MAP` string dispatch, not dead code
- Functions in `__all__` exports — public API, not dead code
- `class Figure: pass` in dashboard_visualizations.py — plotly stub for when plotly isn't installed

### NESTED DICT ACCESS RULES

**DO NOT flag** (internal dicts built in same function):
- `src/utils/storage_analyzer.py:93-99` — `results["projects"]["size"]` — internal dict
- `src/utils/claude/backup_cleanup.py:63-101` — `stats["total_size"]` — internal dict

**DO flag** (external data from config/metadata/API):
- Any `status["latest_backup"]["name"]` — status dict comes from metadata JSON
- Any `config["section"]["key"]` — config from YAML, keys may be missing
- Pattern: if the dict variable was loaded from `json.load()`, `yaml.safe_load()`, or returned from another function → use `.get()`

### WHAT TO LOOK FOR

For type safety (preflight TYPE-01/02/03/04):
- Modernize `from typing import` lines that include deprecated generics
- For each TYPE-03 location, trace the dict to its source: internal build = OK, external data = FLAG

For complexity (preflight QUAL-01/02/03):
- Don't just repeat the file/function list — identify the top 3 worst offenders and suggest specific split points
- For deep nesting: suggest early returns or guard clauses

### PREFLIGHT KNOWN ISSUES (verify each one)

{Inject TYPE-* and QUAL-* findings from preflight JSON here}

### OUTPUT FORMAT

For each finding:
```
[SEVERITY] file:line
  Current: `code as-is`
  Should be: `fixed code`
  Why: one sentence
```

Group into:
- **Auto-fixable** (safe, no logic change): type hints, print→logger, missing annotations
- **Requires review**: complexity splits, nested dict access, dead code removal

Final response under 3000 characters.

---

## Tech Debt Scoring (--debt flag)

When `--debt` is active, assign points:

| Severity | Points Each |
|-|-|
| Critical | 8 |
| High | 4 |
| Medium | 2 |
| Low | 1 |

Cap at 100. Score: 0 = pristine, 25 = healthy, 50 = needs attention, 75+ = significant debt.

Include "Auto-Fixable" section for items fixable without logic changes.

---

## Severity Gating

| Level | Label | Criteria |
|-|-|-|
| BLOCK | Critical | Security vulnerabilities (injection, traversal, credential exposure), data loss potential |
| WARN | High | Missing cache invalidation after mutation, bare except, missing types on public interfaces |
| WARN | Medium | Modernization, dead code, missing null checks, silent exception handlers |
| INFO | Low | Style issues, minor modernization, naming, complexity warnings |

---

## Output Format

```markdown
# Code Review Report
**Target:** [files reviewed]
**Date:** [timestamp]
**Preflight:** [count of fail/warn/pass]

## Summary
| Severity | Count |
|-|-|
| Critical | X |
| High | X |
| Medium | X |
| Low | X |

## Critical Issues (Fix Immediately)
| File | Line | Issue | Current | Should Be | Why |
|-|-|-|-|-|-|

## High Priority
| File | Line | Issue | Current | Should Be | Why |
|-|-|-|-|-|-|

## Medium Priority
| File | Line | Issue | Fix |
|-|-|-|-|

## Low Priority
[Summary count by category, not individual items]

## Auto-Fixable Issues
**Safe (no logic changes):**
- [list with file:line and fix]

**Requires confirmation:**
- [list with reasoning]

## Recommended Fix Order
1. ...

## Tech Debt Score (if --debt)
**Score: X/100**
[Breakdown by category]
```

## Post-Flight Reconciliation

After all agents return:

1. **DEDUP**: Same file:line from multiple agents → keep the one from the owning agent per scope table
2. **PREFLIGHT CHECK**: Every preflight finding with status `fail` or `warn` must appear in an agent report as either:
   - Confirmed (with expanded context and fix)
   - Dismissed (with specific reason why it's a false positive)
   If a preflight finding is missing from all reports, it was dropped. Flag it.
3. **COMPLETENESS**: Reject findings missing file:line, current code, proposed fix, or why.
4. **SEVERITY GATE**: Apply severity levels based on project-specific impact, not generic rules.

## After Review

1. Present summary with severity counts and preflight pass/fail breakdown
2. Show critical issues first with file:line and concrete fix
3. Offer to auto-fix safe issues
4. Suggest priority order for manual fixes

## Workflow Chain

- BLOCK issues found → suggest `/qm-fix` to address them
- Reuse/efficiency concerns → suggest `/qm-simplify`
- Clean review → suggest `/qm-commit`
- Full pipeline → suggest `/qm-ship`
