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
- **Architecture agent gets**: WEB-*, EXC-*, PERF-*, ARCH-*
- **Quality agent gets**: TYPE-*, QUAL-*

New checks added 2026-03-28-v2: SEC-14 (shallow copy secrets), SEC-15 (symlink traversal) → Security. ARCH-03 (non-atomic writes), ARCH-04 (concurrent access) → Architecture. QUAL-09 (unclosed extractfile) → Quality.
New checks added 2026-03-31: QUAL-10 (f-string logging) → Quality. EXC-01 whitelist: metadata.py:20 `except BaseException` in atomic write is correct → Architecture should skip.
Whitelist added 2026-04-07: WEB-03 false positive — `render_config_tab` and `render_project_history_tab` in `src/web/views/claude_code_cleanup.py` are tab helpers invoked from `claude_code.py:62,64`, not page renderers. Architecture should skip these. SEC-14 false positive — `config_manager.py:132-142` was refactored to `{k: v.copy() for k, v in ...}` so the mutation lands on per-item copies, not originals; preflight still flags `.copy()` near password fields but the leak is gone. Verify before flagging.

If the script doesn't exist, report the error and skip preflight (legacy scripts have been consolidated into the comprehensive script).

## Execution: 3 Parallel Sub-Agents

Launch all three simultaneously. Scopes are **strictly non-overlapping**.

| Agent | Owns | Does NOT Check |
|-|-|-|
| **Security** | Command injection, path traversal, credentials, XSS, file permissions, deserialization, SSRF, tempfile safety, unbounded input, shallow copy secrets, symlink traversal | Exception handling, cache patterns, type hints |
| **Architecture** | Cache invalidation/rerun, view/component registration, config access, error handling, backup return patterns, resource leaks, TOCTOU races, error-swallowing returns, non-atomic critical writes, concurrent backup safety | Security, type hints |
| **Quality** | Type modernization, annotations, dead code, complexity, print statements, dict access safety, unguarded next(), unbounded collections, unclosed file handles | Security, cache patterns |

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
2. **Path traversal**: tar extraction safety, restore path validation, user-provided paths, tar arcname injection, metadata JSON path fields
3. **Credential exposure**: hardcoded passwords, secrets in logs/error messages, key file permissions
4. **XSS in Streamlit**: unsafe_allow_html=True with unescaped user data in f-strings
5. **File permissions**: backup files, temp files, config files should be restrictive
6. **Unsafe deserialization**: pickle.load(), yaml.load() without SafeLoader, eval() on untrusted data
7. **SSRF**: requests.get/post with user-controlled URLs without allowlist validation
8. **Tempfile safety**: mkstemp/NamedTemporaryFile(delete=False) without cleanup in finally block (owns: whether cleanup exists; Architecture owns: whether cleanup uses context managers)
9. **Unbounded input**: user-controlled lists/URLs/data accepted without size limits

### KNOWN CORRECT PATTERNS (do not flag these)

- `subprocess.run([...], timeout=N, capture_output=True)` with list args — correct pattern
- `password="{password}"` in `database_ops.py:57` — f-string writing decrypted password to temp MySQL config file, not a hardcoded secret. The password comes from ConfigManager Fernet decryption.
- `st.markdown(f'<div class="status-{level}">...', unsafe_allow_html=True)` where the variable is an internal status string (healthy/warning/critical), not user input — SAFE
- `html.escape()` and `html_mod.escape()` (alias in storage_cleanup.py) wrapping user content — SAFE
- `_safe_extractall()` in project_ops.py validates tar member paths against traversal — SAFE
- `.example` config files are committed to git; real configs are gitignored
- `config/.encryption_key` has 0o600 permissions enforced in config_manager.py:59-65
- `web_scraper.py` has `_is_safe_url()` validation before `requests.get()` — SAFE, not SSRF
- `src/core/backup/database_ops.py` uses `tempfile.mkstemp()` for MySQL config — cleaned up in finally block
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

### Added by review-optimizer [2026-03-28]

For tar arcname injection (preflight SEC-11 reports locations):
- `tar.add(path, arcname=X)` where X comes from config or user input without validation
- If arcname contains `../` or starts with `/`, extracted archive will have traversal paths
- Found: `project_ops.py:213` uses `project_name` from config as arcname. Verify `_validate_identifier()` is called before this point.

For metadata JSON path fields (preflight SEC-12 reports locations):
- JSON loaded from backup metadata files may contain path fields (`base_backup`, `source_path`)
- If these fields contain `../` or absolute paths, they can escape the backup directory
- Found: `project_ops.py:770` loads metadata and uses `base_backup` field for path construction without validation

For unbounded input (preflight SEC-13 reports locations):
- Functions accepting lists/URLs from users without size limits enable resource exhaustion
- Found: `web_scraper.py:269-284` accepts URL lists without count validation
- Check: does the function enforce a maximum before processing?

### Added by review-optimizer [2026-03-28-v2]

For shallow copy leaking secrets (preflight SEC-14 reports locations):
- `dict.copy()` is shallow — nested dicts remain references to the original
- HISTORICAL: `config_manager.py:get_all_databases()` previously mutated inner dicts after a shallow `.copy()`, leaking decrypted passwords into the in-memory config.
- CURRENT (verified 2026-04-07): `config_manager.py:132-142` now builds `{k: v.copy() for k, v in ...}`, so each inner dict is a copy and the subsequent `db_config["password"] = decrypt(...)` mutation lands on the copy, not the original. Preflight SEC-14 still flags lines 124 and 136 because the heuristic matches `.copy()` near password fields — verify the fix is still in place, then dismiss.
- General fix when this pattern IS broken elsewhere: use `copy.deepcopy()` or build a new dict with decrypted values without mutating originals

For symlink traversal in recursive operations (preflight SEC-15 reports locations):
- `Path.rglob("*")` and `os.walk()` follow symlinks by default
- Symlink cycles cause infinite traversal and eventual OOM or hang
- Found: `engine.py` uses `rglob("*")` in `_estimate_project_size()` without `follow_symlinks=False`
- Fix: use `os.walk(path, followlinks=False)` or add `not item.is_symlink()` guard
- NOTE: SEC-15 produces ~38 warnings. Prioritize: (1) backup engine traversal paths that scan user project dirs (highest risk), (2) utility code scanning known-structure dirs (lower risk). Skip rglob in test/analysis code that operates on qmaster's own backup dirs.

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
10. **TOCTOU race conditions**: `Path.exists()` or `os.path.exists()` followed by file operations instead of atomic try-except
11. **Error-swallowing returns**: except blocks that return `""`, `None`, or `[]` without logging, making failures indistinguishable from empty results

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
- `except BaseException` in `src/core/backup/metadata.py:20` — atomic write helper: catches everything to clean up temp file via `os.unlink(tmp)`, then re-raises. This is the correct pattern for ensuring temp file cleanup even on KeyboardInterrupt/SystemExit. Do not flag.
- `except Exception` in `src/core/git_manager.py` (14 instances, lines 79-386) — git operations fail unpredictably; these return `(bool, str)` tuples, which is the correct pattern for this module. Do not flag.
- `except Exception` in `src/core/backup/*.py` — backup mixin methods use the same `(bool, str)` return pattern as git_manager. Broad catches that log and return `(False, msg)` are correct here.
- `render_config_tab` and `render_project_history_tab` in `src/web/views/claude_code_cleanup.py` — these are tab helpers called from `claude_code.py:62,64`, NOT page renderers. The page is `render_claude_code` in `claude_code.py`. Do not flag the tab functions as unregistered (preflight WEB-03 will report them; dismiss with this reasoning).
- `BackupEngine` methods return `(bool, str)` tuples — success `(True, msg)` or failure `(False, msg)`.
- Web state initialized once via `@st.cache_resource` in `web/state.py` — don't flag as missing cache.
- `exists()` checks in CLI display code (listing files, showing status) — TOCTOU is only relevant for create/delete/write operations, not reads for display.

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
- `src/core/backup/*.py` uses the same pattern — broad catch with logging and `(False, msg)` return. Skip these.
- Other files: verify the handler logs the exception or returns a meaningful error. Silent broad catches outside cleanup/git/backup contexts are a code smell.

### Added by review-optimizer [2026-03-28]

For TOCTOU race conditions (preflight ARCH-01 reports locations):
- Pattern: `if path.exists():` followed by `path.unlink()`, `path.write_text()`, or `open(path)` on next lines
- Safe alternative: wrap the operation in try-except (e.g., `try: path.unlink() except FileNotFoundError: pass`)
- Only flag in create/delete/write contexts. Display/listing code using `exists()` is fine.
- Found: file cleanup patterns in cli.py and backup operations

For error-swallowing returns (preflight ARCH-02 reports locations):
- Pattern: `except Exception: return ""` or `except ...: return None` without logging
- The caller can't distinguish between "operation succeeded with empty result" and "operation failed"
- Found: `web_scraper.py:106` returns `""` on Playwright failure, `web_scraper.py:194` returns `""` on parse failure
- Fix: return `None` for failures (distinct from `""` for empty success) and log at debug level

### Added by review-optimizer [2026-03-28-v2]

For non-atomic writes on critical files (preflight ARCH-03 reports locations):
- Pattern: `open(path, "w")` + `json.dump()` or `.write()` on metadata, snapshot, or key files
- If process crashes mid-write, file is truncated/corrupt with no recovery
- Found: `metadata.py:52-100` — backup metadata written directly. `project_ops.py:248` — snapshot JSON written directly.
- Fix: write to temp file in same directory, then `os.rename()` (atomic on same filesystem)
- Only flag for critical data files (metadata, snapshots, keys). Logging/temp files are OK without atomicity.

For concurrent backup safety (preflight ARCH-04 reports locations):
- Pattern: snapshot JSON loaded, long operation runs, snapshot saved — no file locking
- Found: `project_ops.py:148-252` — snapshot loaded at start, tar runs for minutes, snapshot saved at end. Parallel backup of same project overwrites the other's snapshot.
- Found: `src/core/backup/engine.py:240-252` — symlink creation without locking; concurrent backups race on `latest.tar.gz` symlink
- Fix: use `fcntl.flock()` on a lock file per project, or detect concurrent runs and abort

### Added by review-optimizer [2026-04-07]

New module: `src/utils/claude/` is a sub-package containing `advanced_cleanup.py`, `session_inspector.py`, `cleanup.py`, `backup_cleanup.py`, `conversations.py`, `mcp.py`, `stats.py`, `base.py`. These back the new Claude Code page (`src/web/views/claude_code.py` + tab helpers in `src/web/views/claude_code_cleanup.py`).

ARCH-01 hotspots in this new code (preflight may flag some across runs):
- `src/utils/claude/advanced_cleanup.py:326,339` — `Path.exists()` followed by destructive op. Wrap in try-except (`FileNotFoundError`).
- `src/utils/claude/base.py:194` — same TOCTOU pattern.

Tab-helper convention to apply when reviewing the Claude Code page or future multi-tab pages:
- Page renderer (`render_<page>`) lives in its own file, is registered in `PAGE_MAP`
- Tab renderers (`render_<tab>_tab`) live in a sibling file, are imported and called from the page
- Do NOT flag tab renderers as missing from `PAGE_MAP` — they're not pages

### PREFLIGHT KNOWN ISSUES (verify each one)

{Inject WEB-*, EXC-*, ARCH-*, and PERF-* findings from preflight JSON here}

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
7. **Unguarded next()**: `next(x for x in ...)` without default value, raises StopIteration on miss
8. **Unbounded collections**: lists/sets growing in loops without size limits
9. **f-string in logging**: `logging.warning(f"...")` eagerly evaluates even when log level suppressed; use `logging.warning("...", arg)` instead

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

### Added by review-optimizer [2026-03-28]

For unguarded next() (preflight QUAL-07 reports locations):
- `next(x for x in items if ...)` without a default raises `StopIteration` if no match
- Fix: `next((x for x in items if ...), None)` and handle the None case
- Found: `storage_cleanup.py:449` uses unguarded `next()` to find a project by name

For unbounded collections (preflight QUAL-08 reports locations):
- Sets/lists that `.add()` or `.append()` inside loops without checking size
- Found: `web_scraper.py:337` — visited set can grow to 2x max_pages
- Only flag when growth is proportional to external input, not fixed-size internal data

### Added by review-optimizer [2026-03-28-v2]

For unclosed `tar.extractfile()` (preflight QUAL-09 reports locations):
- `tar.extractfile(member)` returns a file-like object that must be closed
- If not wrapped in `with` or followed by `.close()`, file handles leak
- Found: `project_ops.py:868-874` — `file_obj = tar.extractfile(member)` then `content = file_obj.read()` without close
- Fix: use `with tar.extractfile(member) as file_obj:` or add `file_obj.close()` in finally

### Added by review-optimizer [2026-03-31]

For f-string in logging (preflight QUAL-10 reports locations):
- `logging.warning(f"msg {var}")` eagerly evaluates even when log level suppressed
- Fix: `logging.warning("msg %s", var)` — deferred evaluation
- Found: 11 instances across engine.py, dashboard_visualizations.py, storage_cleanup.py, scheduler.py, log_parser.py
- Only flag `logging.debug/info/warning/error(f"...")` — `console.print(f"...")` and `st.error(f"...")` are fine (always displayed)

For `requests.get()` response not in context manager (spot check — no preflight):
- `requests.get()` returns a Response object holding a socket connection
- If not used in `with` block, connection may linger until GC
- Found: `web_scraper.py:165-175` — response used for `.text` and `.headers` but never closed
- Fix: use `with requests.get(...) as resp:` or call `resp.close()` when done
- Only flag in functions that scrape multiple URLs (connection accumulation risk)

### PREFLIGHT KNOWN ISSUES (verify each one)

{Inject TYPE-*, QUAL-* findings from preflight JSON here}

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
