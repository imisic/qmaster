---
paths:
  - "src/core/**/*.py"
  - "src/utils/**/*.py"
---

# File Operation Rules

## Atomic Writes
- Critical files (metadata JSON, snapshot JSON, config) must be written atomically: write to a temp file in the same directory, then `os.rename()` to the target path
- Never write directly to critical files with `open(path, "w")`. A crash mid-write corrupts the file

## TOCTOU (Time-Of-Check-Time-Of-Use)
- Never check `Path.exists()` or `os.path.exists()` then act on the result for destructive operations (unlink, write, rename)
- Use try/except for the operation itself. The file state can change between your check and your action

## File Locking
- Snapshot JSON and symlink updates need file locking when concurrent access is possible
- Parallel backups of the same project can overwrite each other's snapshot without locking
