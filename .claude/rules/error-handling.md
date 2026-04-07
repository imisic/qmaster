---
paths:
  - "src/**/*.py"
---

# Error Handling Rules

## Exception Catching
- Never use bare `except:`. Always catch a specific exception type
- Never catch `BaseException` unless you re-raise it. Catching `BaseException` swallows `SystemExit` and `KeyboardInterrupt`
- Minimize use of broad `except Exception:`. Prefer specific exception types where the failure mode is known

## Silent Failures
- Never use `pass` or `continue` as the sole body of an except block. At minimum, log the error
- Never return empty values (`return ""`, `return None`, `return []`) from exception handlers without logging. This makes failures look like normal results

## Logging
- Never use `print()` in core or utils modules. Use the logger

## Resilience
- Network operations (HTTP requests, git remotes) should retry on transient failures with exponential backoff
- Set reasonable timeouts on all external calls. Don't let a stalled remote hang the process
