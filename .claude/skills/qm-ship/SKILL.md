---
name: qm-ship
description: Ship changes end-to-end for Quartermaster. Chains /qm-review, /qm-fix, and /qm-commit into one pipeline with gates between phases. Use when shipping changes, finishing work, or when the user says "ship", "ship it", "review and commit", "wrap this up", or "finish this".
user_invocable: true
---

# Ship Changes

Chains review, fix, and commit into one flow. Replaces the manual `/qm-review` -> fix -> `/qm-commit` sequence.

**Target:** $ARGUMENTS (default: changed files via `git diff --name-only HEAD`).

## Phase 1: Pre-check

Run `git status` first. If the working tree is clean (no changes), say so and exit. Nothing to ship.

## Phase 2: Review

Invoke `/qm-review $ARGUMENTS` and collect the full report.

If the review finds **zero** high or critical issues, skip Phase 3 and go directly to Phase 4.

## Gate: Review Results

Show the severity summary to the user:
```
Review: X critical, Y high, Z medium, W low
```

- Zero findings: "Clean review. Proceeding to commit."
- Findings present: proceed to fix phase (or skip if `--no-fix`).

## Phase 3: Fix (if needed)

### Critical/High issues
Feed the review report to `/qm-fix` (batch mode). Apply fixes directly for safe changes.

**Safe auto-fixes (apply without asking):**
- Add missing type hints on public functions
- Remove unused imports
- Replace `Optional[X]` with `X | None`
- Replace `List[str]` with `list[str]`
- Add `.get()` with defaults on dict access

**Fixes requiring judgment (apply but note in commit body):**
- Add error handling around subprocess/I/O calls
- Add cache invalidation before `st.rerun()`
- Fix bare `except:` (need to determine correct exception type)

### Medium issues
Fix only if they're in files already being changed. Don't touch unrelated files.

### Low issues
Skip. Style-only changes aren't worth blocking a ship.

### After fixing
Run a **targeted re-review** on only the files modified during fixes. If new critical/high issues appear, fix those too. Maximum 2 fix-review cycles to prevent infinite loops.

## Phase 4: Simplify

Invoke `/qm-simplify` on the changed files. This runs preflight scripts and 3 parallel agents (Reuse, Quality, Efficiency) to catch duplication, dead code, and inefficiency that the review might miss.

If simplify finds and fixes issues, those fixes get included in the commit.

Skip this phase if `--no-simplify` flag is set.

## Phase 5: Commit

Invoke `/qm-commit` with all changes (original + fixes + simplification).

If fixes were applied, include a brief note in the commit body:
```
Also fixes: [one-line summary of review findings addressed]
```

## Phase 6: Summary

Output a brief summary:
```
Shipped: type(scope): commit message
Review: X critical, Y high, Z medium (X fixed, Y skipped)
Files: N files changed
```

## Flags

- `--no-fix`: Skip Phase 3 (fix), commit as-is even with findings
- `--no-simplify`: Skip Phase 4 (simplify), go straight to commit
- `--no-commit`: Run review, fix, and simplify but stop before committing
- `--full`: Pass `--full` to `/qm-review` to scan entire codebase, not just changed files
- `--debt`: Pass `--debt` to `/qm-review` for tech debt scoring

All other flags pass through to `/qm-review`.

## Rules

- Never force-push or amend commits
- Never commit sensitive files (see `/qm-commit` BLOCK list)
- If review finds a BLOCK-level security vulnerability, stop and show it. Don't auto-fix security issues that need human judgment (credential handling, permission checks, encryption logic).
- If there are no changes to ship (clean working tree), say so and exit
