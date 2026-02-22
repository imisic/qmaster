---
paths:
  - "src/web/**/*.py"
---

# Web Layer Rules (Streamlit)

## Cache Invalidation
- After any data mutation (backup, restore, cleanup, config change), call `invalidate()` before `st.rerun()`
- Any view file using `st.rerun()` must import `invalidate` from `web.cache`
- `@st.cache_data` decorators must always include a `ttl=` parameter to prevent stale data

## Reruns
- `st.rerun()` is only valid after a data mutation with `invalidate()`, or after a `session_state` change
- Never call `st.rerun()` without one of those two preceding it

## Registration and Exports
- Every `render_*` function in `web/views/` must be registered in `web/views/__init__.py` and `PAGE_MAP`
- Every component module in `web/components/` must be exported in `web/components/__init__.py`

## Output
- Never use `print()` in the web layer. Use `st.error()`, `st.warning()`, `st.info()`, or the logger
