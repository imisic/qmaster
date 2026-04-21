---
paths:
  - "src/**/*.py"
---

# Code Quality Rules

## Module and Function Size
- Source files should stay under 300 lines. Split into sub-modules when they grow beyond this
- Functions should stay under 50 lines. Extract helper functions when they grow
- Avoid nesting deeper than 4 levels. Use early returns, guard clauses, or extract functions

## Modern Python Types
- Prefer `X | None` over `Optional[X]` in new code. Existing `Optional` usage doesn't need migration
- Use lowercase builtins for generics: `list`, `dict`, `tuple`, `set` instead of `typing.List`, `typing.Dict`, etc.
- All public functions and methods must have return type annotations

## Safe Access Patterns
- Use `.get()` for dictionary access on external/parsed data, never direct bracket notation on untrusted dicts
- Use `next(..., None)` or `next(..., default)` instead of bare `next()` on generators. Bare `next()` raises `StopIteration` if empty

## Dead Code
- Remove commented-out code blocks. Don't keep old code "for reference"
- Remove empty or near-empty files (under 5 non-blank lines)

## Performance
- Never do file I/O (`open()`, `json.load()`, `read_text()`) inside loops. Batch reads before the loop
- Never spawn subprocesses inside loops. Batch into a single command or collect args first

## Logging
- Use lazy formatting in logging calls: `logger.warning("msg %s", var)` not `logger.warning(f"msg {var}")`
- f-strings in log calls cause eager evaluation even when the log level suppresses output

## Resource Safety
- Collections that grow inside loops (`.append()`, `.add()`) must have size limits when processing external input
- `tar.extractfile()` returns a file-like object. Always use it with a `with` block or call `.close()`
