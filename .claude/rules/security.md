---
paths:
  - "src/**/*.py"
---

# Security Rules

## Subprocess Safety
- Never use `shell=True` in subprocess calls
- Never use `os.system()`. Use `subprocess.run()` with a list of arguments
- Always pass commands as a list, never a single string
- Never interpolate variables into command strings (no f-strings, `.format()`, or `%s` in args)
- Always set `timeout=` on `subprocess.run()`, `.call()`, `.wait()`, and `.communicate()`

## Input Handling
- All YAML operations go through ConfigManager, never raw `yaml.load()` or `yaml.dump()`
- Use `yaml.safe_load()` if raw load is unavoidable. Never `yaml.load()` without `Loader=SafeLoader`
- Never use `pickle.load()` or `pickle.loads()` on data that could be untrusted
- For safe literal parsing use `ast.literal_eval()`. Never execute arbitrary expressions from external input

## Secrets and Credentials
- No hardcoded passwords, API keys, or secrets in source code
- Passwords and keys come from environment variables or encrypted config
- Sensitive config files (`config/*.yaml`, `.env`, `.encryption_key`) must stay in `.gitignore`
- Encryption key files must have `0o600` permissions (owner read/write only)
- Never log secrets, passwords, or tokens. Redact sensitive fields before any logging

## XSS Prevention in Streamlit
- When using `unsafe_allow_html=True`, never interpolate variables directly into HTML strings
- All user-controlled values must be escaped with `html.escape()` before HTML embedding

## Network Safety
- Validate URLs against an allowlist before making HTTP requests
- Never pass user-controlled URLs directly to `requests.get()` or `requests.post()`
- HTTP clients must implement rate limiting to avoid overwhelming remote servers

## File and Path Safety
- Validate archive member names before tar extraction. Reject paths containing `../` or starting with `/`
- When using `tar.add()` with `arcname`, validate the name contains no traversal sequences
- Metadata JSON path fields (`base_backup`, `source_path`) must be validated before use
- Use `followlinks=False` with `os.walk()`. Use `Path.rglob()` with caution as it follows symlinks by default

## Shallow Copy Safety
- Never use `dict.copy()` on dictionaries containing password, secret, key, or token fields. Use `copy.deepcopy()` or build a new dict with per-value copies to prevent mutation leaking back to the original

## Temporary Files
- Temporary files created with `mkstemp()` or `NamedTemporaryFile(delete=False)` must be cleaned up in a `finally` block
