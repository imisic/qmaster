---
name: qm-commit
description: Git commit with pre-flight checks for Quartermaster. Scans for sensitive files (config YAML, encryption keys, .env), drafts conventional commit messages with scope detection, stages files individually. Use whenever committing changes, creating commits, or when the user says "commit", "save changes", or "push this".
user_invocable: true
---

# Git Commit

Create a clean, well-structured commit following project conventions.

## Phase 1: Pre-flight Checks

Run these in parallel to assess the working tree:

```bash
git status
git diff --stat
git log --oneline -5
```

Never use `git status -uall` (memory issues on large repos).

### Sensitive File Scan

Scan diff and untracked files for anything that shouldn't be committed:

**BLOCK (stop immediately, show warning):**
- `config/*.yaml` (real configs with credentials/paths, only `.example` files are safe)
- `config/.encryption_key` (Fernet key)
- `.env` or `.env.*` files
- `.claude/settings.local.json` (local permissions and personal paths)
- `*.pem`, `*.key`, `*.p12` (private keys/certs)
- Files containing hardcoded passwords, API keys, or tokens
- `data/` or `backups/` directories (runtime data)

**WARN (show warning, ask to confirm):**
- `__pycache__/` directories in staging
- `CLAUDE.md` changes (project-level, confirm intent)
- `GITHUB_RELEASE_PLAN.md` (internal planning)
- Large unrelated changes across many files (suggest splitting)
- `print()` statements in production code (should use `logger`)
- `.claude/plans/` or `.claude/memory/` files

If blocked, explain what was found and stop. Do not proceed to staging.

## Phase 2: Analyze Changes

Determine the commit type and scope from the diff:

| Type | When |
|-|-|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `style` | Formatting, whitespace only |
| `docs` | Documentation changes |
| `chore` | Build, config, dependency, or maintenance tasks |
| `test` | Test additions or modifications |
| `perf` | Performance improvement |

Determine scope from which area changed:

| Scope | Files |
|-|-|
| `core` | `src/core/*` (backup_engine, config_manager, git_manager) |
| `web` | `src/web/*` (app, views, components, cache, state, theme) |
| `cli` | `src/cli.py` |
| `utils` | `src/utils/*` |
| `config` | `config/*.example`, settings |
| `ui` | `src/web/components/*` only |

## Phase 3: Draft Commit Message

Format: conventional commit with scope.

- Subject line: `type(scope): description` max 50 characters
- Imperative mood ("add" not "added", "fix" not "fixed")
- Optional body after blank line explaining **why**, not what
- Body wraps at 72 characters

**Good:** `feat(web): add web scraper view with URL validation`
**Bad:** `updated the web scraper view to add some new stuff`

If multiple scopes changed, use the primary one or omit scope for cross-cutting changes.

## Phase 4: Stage Files

**Never use `git add .` or `git add -A`.** Stage files individually by name.

Review each file before staging:
- Only stage files relevant to this commit's purpose
- Skip unrelated changes (suggest separate commit)
- Skip sensitive files (see Phase 1 BLOCK list)
- Skip `__pycache__/`, `.pyc` files

## Phase 5: Create Commit

Use a HEREDOC for the message to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
type(scope): subject line here

Optional body explaining why this change was made.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Phase 6: Verify

Run `git status` after commit to confirm clean state or remaining changes.

## Rules

- Never commit broken or half-finished code. If unsure, ask.
- Never use `--force` or `--no-verify`.
- Never amend an existing commit unless the user explicitly asks.
- If a pre-commit hook fails: fix the issue, re-stage, and create a NEW commit. The failed commit never happened, so amending would modify the previous commit.
- Never push unless explicitly asked.
- If user provided a message in arguments, use it (but ensure proper format).

## Workflow Chain

After a clean commit, suggest `/qm-review` if the changes haven't been reviewed yet.
