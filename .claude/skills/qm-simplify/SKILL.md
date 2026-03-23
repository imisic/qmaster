---
name: qm-simplify
description: Review changed code for reuse, quality, and efficiency in Quartermaster. Runs preflight scripts then dispatches 3 parallel agents (Reuse, Quality, Efficiency) on the diff. Fixes issues directly. Use when cleaning up code, simplifying, refactoring, or when the user says "simplify", "clean up", "refactor", or after completing a feature to tighten the code.
user_invocable: true
---

# Simplify: Code Review and Cleanup

Review files for reuse, quality, and efficiency. Fix issues found.

## Phase 1: Run Preflight Scripts

Run the comprehensive preflight script and display the JSON summary:

```bash
bash .claude/scripts/preflight-comprehensive.sh
```

If the comprehensive script doesn't exist, fall back to the legacy scripts:
```bash
bash .claude/scripts/preflight-review.sh.bak
bash .claude/scripts/preflight-perf.sh.bak
bash .claude/scripts/preflight-quality.sh.bak
```

Surface any `fail` or `warn` status items as context for the agents below.

## Phase 2: Launch Three Review Agents in Parallel

Pass each agent the full diff AND the preflight results so they have complete context.

### Agent 1: Code Reuse Review

For each change:

1. **Search for existing utilities and helpers** that could replace newly written code. Check `src/utils/`, `src/core/`, `src/web/components/`, and adjacent files.
2. **Flag any new function that duplicates existing functionality.** Suggest the existing function instead.
3. **Flag inline logic that could use an existing utility** — hand-rolled path handling, manual config reads (should use ConfigManager), custom string manipulation, reinvented error patterns.

Qmaster-specific reuse targets:
- Config access: must go through `ConfigManager`, never direct YAML reads
- Storage paths: use `config.get_storage_paths()`, not hardcoded
- Web components: check `src/web/components/` before creating inline UI
- Cache patterns: use `src/web/cache.py` functions, not ad-hoc caching
- Backup metadata: use existing `_create_metadata()` pattern
- Console output: use Rich `console.print()` in CLI, `st.error()`/`st.success()` in web

### Agent 2: Code Quality Review

Review the same changes for these patterns:

1. **Redundant state**: state that duplicates existing state, cached values that could be derived, Streamlit session_state entries that mirror cache
2. **Parameter sprawl**: adding new parameters to a function instead of restructuring (>4 params = consider splitting)
3. **Copy-paste with variation**: near-duplicate code blocks that should be unified. Especially watch for duplicated sync-copy blocks in backup_engine.py
4. **Leaky abstractions**: exposing internal details (e.g., returning raw YAML dicts instead of typed results), or breaking the layer boundaries (web touching core directly, CLI doing business logic)
5. **Stringly-typed code**: using raw strings where constants or enums should exist (storage keys, backup types, severity levels)
6. **Unnecessary comments**: comments explaining WHAT (the code already does that), narrating changes, or referencing tasks. Keep only non-obvious WHY (hidden constraints, workarounds)
7. **Dead code**: unused imports, unreachable branches, assigned-but-never-read variables

### Agent 3: Efficiency Review

Review the same changes for:

1. **Unnecessary work**: redundant computations, repeated file reads, duplicate subprocess calls, scanning the same directory multiple times
2. **Missed concurrency**: independent operations run sequentially when they could run in parallel (e.g., backup + metadata + sync steps that don't depend on each other)
3. **N+1 patterns**: subprocess/DB calls inside loops, repeated ConfigManager reads in a loop
4. **Missing cache**: expensive web operations without `@st.cache_data`, or cache not invalidated before `st.rerun()`
5. **Unnecessary existence checks**: checking file/directory exists before operating (TOCTOU). Operate directly and handle the error.
6. **Memory**: unbounded lists growing in loops, not using generators for large datasets, holding entire backup archives in memory
7. **Overly broad operations**: reading entire config when only one setting needed, loading all backups when filtering for latest

## Phase 3: Fix Issues

Wait for all three agents to complete. Aggregate findings and fix each issue directly.

If a finding is a false positive or not worth addressing, note it and skip. Don't argue with findings, just skip them.

When done, briefly summarize what was fixed (or confirm the code was already clean).

## Workflow Chain

After simplifying, suggest `/qm-commit` to commit the cleanup.
