#!/bin/bash
# Preflight Comprehensive: All-in-one code quality, security, and pattern checks
# Replaces preflight-review.sh, preflight-perf.sh, preflight-quality.sh
# Outputs JSON with status (pass/warn/fail) per check.
# Always exits 0 — use the JSON status field, not exit codes.

SRC_DIR="${1:-src}"
cd "$(git rev-parse --show-toplevel)" || exit 0

python3 /dev/stdin "$SRC_DIR" <<'PYTHON_SCRIPT'
import json
import os
import re
import subprocess
import sys

SRC_DIR = sys.argv[1] if len(sys.argv) > 1 else "src"
MAX_LOCATIONS = 20
results = {}


def find_py_files(directory, exclude_pycache=True):
    """Recursively find all .py files in directory."""
    files = []
    if not os.path.isdir(directory):
        return files
    for root, dirs, filenames in os.walk(directory):
        if exclude_pycache:
            dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py"):
                files.append(os.path.join(root, f))
    return sorted(files)


def rel_path(p):
    """Return path relative to git root, strip leading ./"""
    if p.startswith("./"):
        p = p[2:]
    return p


def rg_search(pattern, path=None, extra_args=None):
    """Run ripgrep and return list of (file, lineno, line) tuples."""
    cmd = ["rg", "-n", "--no-heading", pattern]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["--glob", "*.py", "--glob", "!__pycache__/**"])
    if path:
        cmd.append(path)
    else:
        cmd.append(SRC_DIR)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        hits = []
        for line in out.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) >= 3:
                hits.append((rel_path(parts[0]), int(parts[1]), parts[2]))
            elif len(parts) == 2:
                hits.append((rel_path(parts[0]), int(parts[1]), ""))
        return hits
    except Exception:
        return []


def make_result(check_id, status, count, locations, message):
    results[check_id] = {
        "status": status,
        "count": count,
        "locations": locations[:MAX_LOCATIONS],
        "message": message,
    }


def read_file(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def read_lines(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def extract_full_call(lines, start_idx, col_offset=0):
    """Extract full function call from start line to matching closing paren."""
    text = ""
    depth = 0
    started = False
    for i in range(start_idx, min(start_idx + 50, len(lines))):
        line = lines[i]
        if i == start_idx and col_offset:
            line = line[col_offset:]
        for ch in line:
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
            text += ch
            if started and depth == 0:
                return text, i
    return text, min(start_idx + 50, len(lines) - 1)


# =========================================================================
# GROUP A: Security
# =========================================================================

# SEC-01: shell=True in subprocess calls
hits = rg_search(r"shell\s*=\s*True", SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
status = "fail" if locs else "pass"
make_result("SEC-01", status, len(locs), locs,
            f"shell=True in subprocess: {len(locs)} found")

# SEC-02: os.system() calls
hits = rg_search(r"os\.system\(", SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
status = "fail" if locs else "pass"
make_result("SEC-02", status, len(locs), locs,
            f"os.system() calls: {len(locs)} found")

# SEC-03: Hardcoded secrets
hits = rg_search(r'(?i)(password|api_key|secret)\s*=\s*["\x27]', SRC_DIR)
filtered = []
exclude_pats = ["getenv", "get_setting", ".get(", "os.environ", "# example",
                "# noqa", "{", "enc:"]
for f, ln, content in hits:
    if not any(ep in content for ep in exclude_pats):
        filtered.append(f"{f}:{ln}")
status = "fail" if filtered else "pass"
make_result("SEC-03", status, len(filtered), filtered,
            f"Hardcoded secrets: {len(filtered)} found")

# SEC-04: unsafe_allow_html=True with unescaped f-string interpolation
sec04_locs = []
web_dir = os.path.join(SRC_DIR, "web")
if os.path.isdir(web_dir):
    fstring_pat = re.compile(r'f["\x27](.*?\{.*?\}.*?)["\x27]', re.DOTALL)
    brace_pat = re.compile(r"\{([^}]+)\}")
    for pyfile in find_py_files(web_dir):
        lines = read_lines(pyfile)
        for i, line in enumerate(lines):
            if "unsafe_allow_html=True" not in line:
                continue
            search_start = max(0, i - 15)
            block = "".join(lines[search_start:i + 1])
            fstring_matches = fstring_pat.findall(block)
            if not fstring_matches:
                continue
            has_unescaped = False
            for m in fstring_matches:
                exprs = brace_pat.findall(m)
                for expr in exprs:
                    es = expr.strip()
                    if (es and
                            "html.escape(" not in es and
                            "html_mod.escape(" not in es and
                            not es.startswith(("len(", "i ", "i+", "i-"))):
                        has_unescaped = True
            if has_unescaped:
                sec04_locs.append(f"{rel_path(pyfile)}:{i + 1}")
status = "warn" if sec04_locs else "pass"
make_result("SEC-04", status, len(sec04_locs), sec04_locs,
            f"unsafe_allow_html with unescaped f-string: {len(sec04_locs)} found (verify user-input risk)")

# SEC-05: Direct YAML load/dump outside ConfigManager
hits = rg_search(r"yaml\.(safe_load|load|dump)\b", SRC_DIR)
filtered = [f"{f}:{ln}" for f, ln, _ in hits if "config_manager" not in f]
status = "warn" if filtered else "pass"
make_result("SEC-05", status, len(filtered), filtered,
            f"YAML usage outside ConfigManager: {len(filtered)} found")

# SEC-06: String interpolation in subprocess args
hits = rg_search(r"subprocess\.", SRC_DIR)
sec06_locs = []
fstr_pat = re.compile(r'f["\x27]')
for f, ln, content in hits:
    if fstr_pat.search(content) or ".format(" in content or "%s" in content:
        sec06_locs.append(f"{f}:{ln}")
status = "fail" if sec06_locs else "pass"
make_result("SEC-06", status, len(sec06_locs), sec06_locs,
            f"String interpolation in subprocess args: {len(sec06_locs)} found")

# SEC-07: Sensitive files tracked in git
sec07_locs = []
for pattern in ["config/*.yaml", ".env", ".env.*", "config/.encryption_key"]:
    try:
        out = subprocess.run(["git", "ls-files", pattern], capture_output=True,
                             text=True, timeout=10)
        for line in out.stdout.strip().splitlines():
            if line.strip():
                sec07_locs.append(rel_path(line.strip()))
    except Exception:
        pass
status = "fail" if sec07_locs else "pass"
make_result("SEC-07", status, len(sec07_locs), sec07_locs,
            f"Sensitive files tracked in git: {len(sec07_locs)} found")


# =========================================================================
# GROUP B: Subprocess Safety (Python-based multi-line detection)
# =========================================================================

sub01_locs = []
sub02_locs = []
sub03_locs = []

subprocess_call_funcs = {"subprocess.run", "subprocess.call",
                         "subprocess.check_output", "subprocess.check_call"}

for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # SUB-01: subprocess calls without timeout
        for func in subprocess_call_funcs:
            if func in line:
                full_call, end_line = extract_full_call(lines, i)
                if "timeout=" not in full_call and "timeout =" not in full_call:
                    sub01_locs.append(f"{rpath}:{i + 1}")

        # SUB-02: Popen without .wait(timeout=) or .communicate(timeout=)
        if "subprocess.Popen(" in line:
            var_match = re.match(r"\s*(\w+)\s*=\s*subprocess\.Popen\(", line)
            if var_match:
                varname = var_match.group(1)
                found_timeout = False
                for j in range(i + 1, min(i + 31, len(lines))):
                    fwd_line = lines[j]
                    if (varname + ".wait(timeout=" in fwd_line or
                            varname + ".wait( timeout=" in fwd_line or
                            varname + ".communicate(timeout=" in fwd_line or
                            varname + ".communicate( timeout=" in fwd_line):
                        found_timeout = True
                        break
                if not found_timeout:
                    sub02_locs.append(f"{rpath}:{i + 1}")
            else:
                sub02_locs.append(f"{rpath}:{i + 1}")

        # SUB-03: subprocess called with single string instead of list
        for func in list(subprocess_call_funcs) + ["subprocess.Popen"]:
            if func in line:
                full_call, _ = extract_full_call(lines, i)
                paren_pos = full_call.find("(")
                if paren_pos >= 0:
                    after_paren = full_call[paren_pos + 1:].lstrip()
                    if after_paren and after_paren[0] in ('"', "'"):
                        sub03_locs.append(f"{rpath}:{i + 1}")
                    elif after_paren.startswith('f"') or after_paren.startswith("f'"):
                        sub03_locs.append(f"{rpath}:{i + 1}")

# Deduplicate preserving order
sub01_locs = list(dict.fromkeys(sub01_locs))
sub02_locs = list(dict.fromkeys(sub02_locs))
sub03_locs = list(dict.fromkeys(sub03_locs))

make_result("SUB-01", "fail" if sub01_locs else "pass", len(sub01_locs), sub01_locs,
            f"subprocess calls without timeout=: {len(sub01_locs)} found")
make_result("SUB-02", "warn" if sub02_locs else "pass", len(sub02_locs), sub02_locs,
            f"Popen without timeout on wait/communicate: {len(sub02_locs)} found")
make_result("SUB-03", "fail" if sub03_locs else "pass", len(sub03_locs), sub03_locs,
            f"subprocess with string arg instead of list: {len(sub03_locs)} found")


# =========================================================================
# GROUP C: Exception Handling
# =========================================================================

# EXC-01: except BaseException (whitelist: atomic write cleanup that re-raises)
hits = rg_search(r"except\s+BaseException\b", SRC_DIR)
exc01_locs = []
for f, ln, content in hits:
    # Whitelist: atomic write pattern (except BaseException: cleanup + raise)
    pyfile = f
    flines = read_lines(pyfile)
    if ln - 1 < len(flines):
        # Check next 5 lines for raise (cleanup-then-reraise pattern)
        window = "".join(flines[ln - 1:min(ln + 4, len(flines))])
        if "raise" in window and ("unlink" in window or "remove" in window):
            continue  # Atomic write cleanup — correct pattern
    exc01_locs.append(f"{f}:{ln}")
make_result("EXC-01", "fail" if exc01_locs else "pass", len(exc01_locs), exc01_locs,
            f"except BaseException: {len(exc01_locs)} found")

# EXC-02: Bare except:
hits = rg_search(r"^\s*except\s*:", SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
make_result("EXC-02", "fail" if locs else "pass", len(locs), locs,
            f"Bare except: {len(locs)} found")

# EXC-03: Silent exception handler
exc03_locs = []
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        except_match = re.match(r"(\s*)except\b.*:", line)
        if not except_match:
            continue
        indent = len(except_match.group(1))
        body_indent = indent + 4
        body_lines = []
        for j in range(i + 1, min(i + 20, len(lines))):
            bline = lines[j]
            if bline.strip() == "":
                continue
            cur_indent = len(bline) - len(bline.lstrip())
            if cur_indent <= indent and bline.strip():
                break
            if cur_indent >= body_indent:
                body_lines.append(bline.strip())

        if not body_lines:
            continue

        silent_keywords = {"pass", "continue", "raise"}
        all_silent = all(bl in silent_keywords for bl in body_lines)
        if not all_silent:
            continue

        has_logging = any(kw in bl for bl in body_lines
                         for kw in ["log", "logger", "logging"])
        if has_logging:
            continue

        # Whitelist: cleanup context
        except_text = line.strip()
        is_cleanup = False
        if "OSError" in except_text or "PermissionError" in except_text:
            pre_lines = "".join(lines[max(0, i - 5):i])
            if any(kw in pre_lines for kw in ["unlink", "remove", "rmdir"]):
                is_cleanup = True

        if not is_cleanup:
            exc03_locs.append(f"{rpath}:{i + 1}")

make_result("EXC-03", "warn" if exc03_locs else "pass", len(exc03_locs), exc03_locs,
            f"Silent exception handlers: {len(exc03_locs)} found")

# EXC-04: Broad except Exception count
hits = rg_search(r"except\s+Exception\b", SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
make_result("EXC-04", "info", len(locs), locs,
            f"Broad except Exception: {len(locs)} instances")


# =========================================================================
# GROUP D: Streamlit/Web Patterns
# =========================================================================

web_views_dir = os.path.join(SRC_DIR, "web", "views")
web_comp_dir = os.path.join(SRC_DIR, "web", "components")

# WEB-01: st.rerun() without invalidate() in preceding 10 lines
web01_locs = []
if os.path.isdir(web_dir):
    cancel_pat = re.compile(r'["\'](cancel|close|dismiss)["\']', re.IGNORECASE)
    uionly_pat = re.compile(r'["\'](select all|deselect all|top \d+)["\']', re.IGNORECASE)
    for pyfile in find_py_files(web_dir):
        lines = read_lines(pyfile)
        rpath = rel_path(pyfile)
        for i, line in enumerate(lines):
            if "st.rerun()" not in line:
                continue
            start = max(0, i - 10)
            preceding = "".join(lines[start:i])

            if "invalidate(" in preceding:
                continue

            # Whitelist: cancel/close/dismiss button
            if cancel_pat.search(preceding):
                continue

            # Whitelist: Select All / Deselect All / Top N buttons
            if uionly_pat.search(preceding):
                continue

            # Whitelist: only session_state assignments + safe Streamlit UI calls
            pre_lines_stripped = [l.strip() for l in lines[start:i]
                                 if l.strip() and not l.strip().startswith("#")]
            session_only = True
            safe_calls = ["st.button", "st.selectbox", "st.number_input",
                          "st.text_input", "st.checkbox", "st.radio",
                          "st.columns", "st.session_state", "st.rerun"]
            for pl in pre_lines_stripped:
                if "st.session_state" in pl:
                    continue
                if any(sc in pl for sc in safe_calls):
                    continue
                if re.search(r"\w+\(", pl):
                    ctrl_kws = ["if ", "elif ", "else:", "for ", "while ",
                                "def ", "class ", "return", "import", "with "]
                    if not any(kw in pl for kw in ctrl_kws):
                        session_only = False
                        break
            if session_only and pre_lines_stripped:
                continue

            web01_locs.append(f"{rpath}:{i + 1}")

make_result("WEB-01", "fail" if web01_locs else "pass", len(web01_locs), web01_locs,
            f"st.rerun() without invalidate(): {len(web01_locs)} found")

# WEB-02: View file uses st.rerun() but doesn't import invalidate from web.cache
web02_locs = []
if os.path.isdir(web_views_dir):
    for pyfile in find_py_files(web_views_dir):
        if os.path.basename(pyfile) == "__init__.py":
            continue
        content = read_file(pyfile)
        if "st.rerun()" not in content:
            continue
        has_invalidate_import = "invalidate" in content and ("web.cache" in content or "from web" in content)
        if not has_invalidate_import:
            web02_locs.append(rel_path(pyfile))

make_result("WEB-02", "fail" if web02_locs else "pass", len(web02_locs), web02_locs,
            f"View files with st.rerun() but no invalidate import: {len(web02_locs)} found")

# WEB-03: render_* function not registered in __init__.py
web03_locs = []
init_path = os.path.join(web_views_dir, "__init__.py")
init_content = read_file(init_path) if os.path.isfile(init_path) else ""
if os.path.isdir(web_views_dir):
    for pyfile in find_py_files(web_views_dir):
        if os.path.basename(pyfile) == "__init__.py":
            continue
        flines = read_lines(pyfile)
        rpath = rel_path(pyfile)
        for idx, fline in enumerate(flines):
            match = re.match(r"def (render_\w+)\(", fline)
            if match:
                fname = match.group(1)
                if fname not in init_content:
                    web03_locs.append(f"{rpath}:{idx + 1}:{fname}")

make_result("WEB-03", "fail" if web03_locs else "pass", len(web03_locs), web03_locs,
            f"Unregistered render_* functions: {len(web03_locs)} found")

# WEB-04: Component file without exports in __init__.py
web04_locs = []
comp_init = os.path.join(web_comp_dir, "__init__.py")
comp_init_content = read_file(comp_init) if os.path.isfile(comp_init) else ""
if os.path.isdir(web_comp_dir):
    for pyfile in find_py_files(web_comp_dir):
        basename = os.path.basename(pyfile)
        if basename == "__init__.py":
            continue
        module_name = basename.replace(".py", "")
        if module_name not in comp_init_content:
            web04_locs.append(rel_path(pyfile))

make_result("WEB-04", "warn" if web04_locs else "pass", len(web04_locs), web04_locs,
            f"Component files without exports: {len(web04_locs)} found")

# WEB-05: print() in web layer
web05_locs = []
if os.path.isdir(web_dir):
    hits = rg_search(r"\bprint\(", web_dir)
    web05_locs = [f"{f}:{ln}" for f, ln, content in hits
                  if "console.print" not in content and "# noqa" not in content]
make_result("WEB-05", "fail" if web05_locs else "pass", len(web05_locs), web05_locs,
            f"print() in web layer: {len(web05_locs)} found")

# WEB-06: @st.cache_data without TTL
web06_locs = []
if os.path.isdir(web_dir):
    for pyfile in find_py_files(web_dir):
        flines = read_lines(pyfile)
        rpath = rel_path(pyfile)
        for idx, fline in enumerate(flines):
            stripped = fline.strip()
            if stripped.startswith("@st.cache_data"):
                combined = fline
                if idx + 1 < len(flines):
                    combined += flines[idx + 1]
                if "ttl" not in combined:
                    web06_locs.append(f"{rpath}:{idx + 1}")

make_result("WEB-06", "warn" if web06_locs else "pass", len(web06_locs), web06_locs,
            f"@st.cache_data without TTL: {len(web06_locs)} found")

# WEB-07: Orphan st.rerun() - no session_state mutation AND no invalidate
web07_locs = []
if os.path.isdir(web_dir):
    for pyfile in find_py_files(web_dir):
        flines = read_lines(pyfile)
        rpath = rel_path(pyfile)
        for idx, fline in enumerate(flines):
            if "st.rerun()" not in fline:
                continue
            start = max(0, idx - 10)
            preceding = "".join(flines[start:idx])
            has_inv = "invalidate(" in preceding
            has_ss = "st.session_state" in preceding
            if not has_inv and not has_ss:
                web07_locs.append(f"{rpath}:{idx + 1}")

make_result("WEB-07", "warn" if web07_locs else "pass", len(web07_locs), web07_locs,
            f"Orphan st.rerun() (no state mutation or invalidate): {len(web07_locs)} found")


# =========================================================================
# GROUP E: Type Safety
# =========================================================================

# TYPE-01: Old typing imports (Optional, List, Dict, Tuple, Set)
hits = rg_search(r"from typing import", SRC_DIR)
type01_locs = []
old_types = {"Optional", "List", "Dict", "Tuple", "Set"}
for f, ln, content in hits:
    after_import = content.split("import", 1)[-1] if "import" in content else ""
    imported_names = {n.strip().rstrip(",") for n in after_import.split(",")}
    if imported_names & old_types:
        type01_locs.append(f"{f}:{ln}")

make_result("TYPE-01", "warn" if type01_locs else "pass", len(type01_locs), type01_locs,
            f"Old typing imports (Optional/List/Dict/Tuple/Set): {len(type01_locs)} found")

# TYPE-02: Public function missing return type annotation
type02_locs = []
for pyfile in find_py_files(SRC_DIR):
    flines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for idx, fline in enumerate(flines):
        match = re.match(r"( {0,4})def (\w+)\(", fline)
        if not match:
            continue
        indent_str = match.group(1)
        fname = match.group(2)
        if fname.startswith("_"):
            continue
        if len(indent_str) not in (0, 4):
            continue
        has_arrow = False
        for j in range(idx, min(idx + 5, len(flines))):
            if "->" in flines[j]:
                has_arrow = True
                break
            if flines[j].rstrip().endswith(":") and j > idx:
                break
        if not has_arrow:
            type02_locs.append(f"{rpath}:{idx + 1}:{fname}")

count_missing = len(type02_locs)
status = "warn" if count_missing > 20 else "info"
make_result("TYPE-02", status, count_missing, type02_locs,
            f"Public functions missing return type: {count_missing} found")

# TYPE-03: Unsafe nested dict access
hits = rg_search(r'\["[^"]+"\]\["[^"]+"\]', SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
make_result("TYPE-03", "warn" if locs else "pass", len(locs), locs,
            f"Nested dict access on external data: {len(locs)} found (verify manually)")

# TYPE-04: Old-style typing.Dict, typing.List, etc.
hits = rg_search(r"typing\.(Dict|List|Tuple|Set)\b", SRC_DIR)
locs = [f"{f}:{ln}" for f, ln, _ in hits]
make_result("TYPE-04", "warn" if locs else "pass", len(locs), locs,
            f"Old-style typing.X usage: {len(locs)} found")


# =========================================================================
# GROUP F: Code Quality
# =========================================================================

# QUAL-01: Files > 300 lines
qual01_locs = []
for pyfile in find_py_files(SRC_DIR):
    flines = read_lines(pyfile)
    if len(flines) > 300:
        qual01_locs.append(f"{rel_path(pyfile)}:{len(flines)} lines")

count_large = len(qual01_locs)
if count_large == 0:
    status = "pass"
elif count_large <= 5:
    status = "warn"
else:
    status = "fail"
make_result("QUAL-01", status, count_large, qual01_locs,
            f"Files over 300 lines: {count_large} found")

# QUAL-02: Functions > 50 lines
qual02_locs = []
for pyfile in find_py_files(SRC_DIR):
    flines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    func_starts = []
    for idx, fline in enumerate(flines):
        match = re.match(r"(\s*)def (\w+)\(", fline)
        if match:
            indent_len = len(match.group(1))
            func_starts.append((idx, indent_len, match.group(2)))

    for fidx, (start, indent, fname) in enumerate(func_starts):
        end = len(flines)
        for next_start, next_indent, _ in func_starts[fidx + 1:]:
            if next_indent <= indent:
                end = next_start
                break
        func_lines = end - start
        if func_lines > 50:
            qual02_locs.append(f"{rpath}:{start + 1}:{fname} ({func_lines} lines)")

make_result("QUAL-02", "warn" if qual02_locs else "pass", len(qual02_locs), qual02_locs,
            f"Functions over 50 lines: {len(qual02_locs)} found")

# QUAL-03: Deep nesting (5+ indent levels = 20+ leading spaces)
hits = rg_search(r"^ {20,}\S", SRC_DIR)
filtered = []
for f, ln, content in hits:
    s = content.strip()
    if not s.startswith("#") and not s.startswith('"""') and not s.startswith("'''"):
        filtered.append(f"{f}:{ln}")

count_deep = len(filtered)
if count_deep == 0:
    status = "pass"
elif count_deep <= 10:
    status = "warn"
else:
    status = "fail"
make_result("QUAL-03", status, count_deep, filtered,
            f"Lines at 5+ indent levels: {count_deep} found")

# QUAL-04: print() in non-web source code
qual04_locs = []
for d in ["src/core", "src/utils"]:
    if os.path.isdir(d):
        hits = rg_search(r"\bprint\(", d)
        for f, ln, content in hits:
            if "console.print" not in content and "# noqa" not in content:
                qual04_locs.append(f"{f}:{ln}")
if os.path.isfile("src/cli.py"):
    hits = rg_search(r"\bprint\(", "src/cli.py")
    for f, ln, content in hits:
        if "console.print" not in content and "# noqa" not in content:
            qual04_locs.append(f"{f}:{ln}")

make_result("QUAL-04", "warn" if qual04_locs else "pass", len(qual04_locs), qual04_locs,
            f"print() in non-web source: {len(qual04_locs)} found")

# QUAL-05: Commented-out code (3+ consecutive # lines with code patterns)
qual05_locs = []
code_pat = re.compile(r'[=()]|\breturn\b|\bif \b|\bfor \b|\bwhile \b|\bclass \b|\bdef \b')
skip_pat = re.compile(r"\b(TODO|FIXME|NOTE|HACK|XXX)\b", re.IGNORECASE)
for pyfile in find_py_files(SRC_DIR):
    flines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    consecutive = 0
    start_line = 0
    for idx, fline in enumerate(flines):
        stripped = fline.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            comment_body = stripped[1:].strip()
            if code_pat.search(comment_body) and not skip_pat.search(comment_body):
                if consecutive == 0:
                    start_line = idx + 1
                consecutive += 1
            else:
                if consecutive >= 3:
                    qual05_locs.append(f"{rpath}:{start_line}-{start_line + consecutive - 1}")
                consecutive = 0
        else:
            if consecutive >= 3:
                qual05_locs.append(f"{rpath}:{start_line}-{start_line + consecutive - 1}")
            consecutive = 0
    if consecutive >= 3:
        qual05_locs.append(f"{rpath}:{start_line}-{start_line + consecutive - 1}")

make_result("QUAL-05", "warn" if qual05_locs else "pass", len(qual05_locs), qual05_locs,
            f"Blocks of commented-out code: {len(qual05_locs)} found")

# QUAL-06: Dead/empty files (< 5 non-blank lines)
qual06_locs = []
for pyfile in find_py_files(SRC_DIR):
    basename = os.path.basename(pyfile)
    if basename in ("__init__.py", "conftest.py"):
        continue
    flines = read_lines(pyfile)
    non_blank = sum(1 for l in flines if l.strip())
    if non_blank < 5:
        qual06_locs.append(f"{rel_path(pyfile)} ({non_blank} lines)")

make_result("QUAL-06", "warn" if qual06_locs else "pass", len(qual06_locs), qual06_locs,
            f"Dead/empty files: {len(qual06_locs)} found")


# =========================================================================
# GROUP G: Additional Security (added by review-optimizer 2026-03-23)
# =========================================================================

# SEC-08: Unsafe deserialization (pickle, yaml.load without SafeLoader, eval)
sec08_locs = []
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "pickle.load" in stripped or "pickle.loads" in stripped:
            sec08_locs.append(f"{rpath}:{i + 1}: pickle deserialization")
        if "yaml.load(" in stripped and "SafeLoader" not in stripped and "safe_load" not in stripped:
            sec08_locs.append(f"{rpath}:{i + 1}: yaml.load without SafeLoader")
        if re.search(r"\beval\s*\(", stripped) and "literal_eval" not in stripped:
            sec08_locs.append(f"{rpath}:{i + 1}: eval() usage")

make_result("SEC-08", "fail" if sec08_locs else "pass", len(sec08_locs), sec08_locs,
            f"Unsafe deserialization: {len(sec08_locs)} found")

# SEC-09: SSRF — requests with variable URLs without validation
sec09_locs = []
req_pat = re.compile(r"requests\.(get|post|put|delete|head|patch)\s*\(")
for pyfile in find_py_files(SRC_DIR):
    text = read_file(pyfile)
    rpath = rel_path(pyfile)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if req_pat.search(line):
            # Check if _is_safe_url or allowlist validation exists in same function/file
            window = "\n".join(lines[max(0, i - 40):i + 1])
            if "_is_safe_url" in window or "_is_safe_url" in text or "allowlist" in window or "ALLOWED" in window:
                continue
            # Check if URL is a hardcoded string (safe)
            if re.search(r'requests\.\w+\(\s*["\x27]https?://', line):
                continue
            sec09_locs.append(f"{rpath}:{i + 1}")

make_result("SEC-09", "warn" if sec09_locs else "pass", len(sec09_locs), sec09_locs,
            f"requests with variable URL (possible SSRF): {len(sec09_locs)} found")

# SEC-10: Tempfile created with delete=False without cleanup in finally
sec10_locs = []
tempfile_pat = re.compile(r"(mkstemp|NamedTemporaryFile.*delete\s*=\s*False)")
for pyfile in find_py_files(SRC_DIR):
    text = read_file(pyfile)
    rpath = rel_path(pyfile)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if tempfile_pat.search(line):
            # Check if there's a finally block within 50 lines that does cleanup
            window = "\n".join(lines[i:min(i + 50, len(lines))])
            has_finally = "finally:" in window
            has_unlink = "os.unlink" in window or "os.remove" in window or "Path(" in window and ".unlink" in window
            if not (has_finally and has_unlink):
                sec10_locs.append(f"{rpath}:{i + 1}")

make_result("SEC-10", "warn" if sec10_locs else "pass", len(sec10_locs), sec10_locs,
            f"Tempfile without cleanup in finally: {len(sec10_locs)} found")


# =========================================================================
# GROUP H: Performance (added by review-optimizer 2026-03-23)
# =========================================================================

# PERF-01: File I/O inside loops (json.load, open, read_text in for/while body)
perf01_locs = []
io_pat = re.compile(r"(json\.load|open\(|\.read_text\(\)|yaml\.safe_load)")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    in_loop_indent = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if re.match(r"\s*(for|while)\s+.*:", line) and not stripped.startswith("#"):
            in_loop_indent = indent
        elif in_loop_indent >= 0:
            if indent <= in_loop_indent and stripped and not stripped.startswith("#"):
                in_loop_indent = -1
            elif indent > in_loop_indent and io_pat.search(stripped):
                perf01_locs.append(f"{rpath}:{i + 1}")

make_result("PERF-01", "warn" if perf01_locs else "pass", len(perf01_locs),
            perf01_locs[:MAX_LOCATIONS],
            f"File I/O inside loops: {len(perf01_locs)} found")

# PERF-02: Subprocess calls inside loops
perf02_locs = []
sub_pat = re.compile(r"subprocess\.(run|call|check_output|check_call|Popen)")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    in_loop_indent = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if re.match(r"\s*(for|while)\s+.*:", line) and not stripped.startswith("#"):
            in_loop_indent = indent
        elif in_loop_indent >= 0:
            if indent <= in_loop_indent and stripped and not stripped.startswith("#"):
                in_loop_indent = -1
            elif indent > in_loop_indent and sub_pat.search(stripped):
                perf02_locs.append(f"{rpath}:{i + 1}")

make_result("PERF-02", "warn" if perf02_locs else "pass", len(perf02_locs),
            perf02_locs[:MAX_LOCATIONS],
            f"Subprocess calls inside loops: {len(perf02_locs)} found")


# =========================================================================
# GROUP I: Security - Extended (added by review-optimizer 2026-03-28)
# =========================================================================

# SEC-11: tar.add() with arcname from variable (potential path injection)
sec11_locs = []
tar_add_pat = re.compile(r"tar\.add\(.*arcname\s*=\s*(\w+)")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        m = tar_add_pat.search(line)
        if m:
            varname = m.group(1)
            # Check entire method scope for validation of the variable
            # Find enclosing method (indent level 4 for class methods)
            func_start = i
            for k in range(i - 1, -1, -1):
                if re.match(r"    def ", lines[k]):
                    func_start = k
                    break
                elif re.match(r"(def |class )", lines[k]):
                    func_start = k
                    break
            func_body = "".join(lines[func_start:i])
            if "_validate_identifier" not in func_body and "re.match" not in func_body:
                sec11_locs.append(f"{rpath}:{i + 1}: arcname={varname}")

make_result("SEC-11", "warn" if sec11_locs else "pass", len(sec11_locs), sec11_locs,
            f"tar.add() with unvalidated arcname: {len(sec11_locs)} found")

# SEC-12: JSON metadata loaded and used for path construction
sec12_locs = []
json_load_pat = re.compile(r"json\.load[s]?\(")
path_field_pat = re.compile(r'\[[\'"](path|file|base_backup|source|target|dir|directory)[\'"]\]')
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    in_json_func = False
    for i, line in enumerate(lines):
        if json_load_pat.search(line):
            in_json_func = True
        if in_json_func and path_field_pat.search(line):
            # Check if the path field is validated
            window = "".join(lines[i:min(i + 5, len(lines))])
            if "_validate" not in window and "re.match" not in window and ".." not in window:
                sec12_locs.append(f"{rpath}:{i + 1}")
        if in_json_func and i > 0 and lines[i].strip().startswith("def "):
            in_json_func = False

make_result("SEC-12", "warn" if sec12_locs else "pass", len(sec12_locs), sec12_locs,
            f"JSON metadata path fields without validation: {len(sec12_locs)} found")

# SEC-13: Functions accepting lists without size limits
sec13_locs = []
list_param_pat = re.compile(r"def \w+\(.*\blist\[", re.IGNORECASE)
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if list_param_pat.search(line) or (": list" in line and "def " in line):
            # Check next 10 lines for size validation
            body = "".join(lines[i:min(i + 10, len(lines))])
            if "len(" not in body and "max_" not in body and "limit" not in body:
                sec13_locs.append(f"{rpath}:{i + 1}")

make_result("SEC-13", "info" if sec13_locs else "pass", len(sec13_locs),
            sec13_locs[:MAX_LOCATIONS],
            f"Functions accepting lists without size check: {len(sec13_locs)} found")


# =========================================================================
# GROUP J: Architecture (added by review-optimizer 2026-03-28)
# =========================================================================

# ARCH-01: TOCTOU — exists() followed by destructive file operation
arch01_locs = []
exists_pat = re.compile(r"\.(exists|is_file|is_dir)\(\)")
# Only flag destructive ops (unlink, write, rename), not mkdir (usually has exist_ok)
destructive_op_pat = re.compile(r"\.(unlink|write_text|write_bytes|rename|replace)\(")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if exists_pat.search(line) and ("if " in line or "not " in line):
            # Check next 5 lines for destructive file operations
            for j in range(i + 1, min(i + 6, len(lines))):
                if destructive_op_pat.search(lines[j]):
                    # Skip if inside try block or if the exists check guards creation
                    pre = "".join(lines[max(0, i - 3):i])
                    post = lines[j].strip()
                    if "try:" not in pre and "exist_ok" not in post:
                        arch01_locs.append(f"{rpath}:{i + 1}")
                    break

make_result("ARCH-01", "warn" if arch01_locs else "pass", len(arch01_locs),
            arch01_locs[:MAX_LOCATIONS],
            f"TOCTOU: exists() then file op without try: {len(arch01_locs)} found")

# ARCH-02: Error-swallowing returns (except that returns empty without logging)
arch02_locs = []
empty_return_pat = re.compile(r'return\s+(""|\'\'|\[\]|None|\{\})\s*$')
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    in_except = False
    except_indent = -1
    except_line = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if re.match(r"\s*except\b", line):
            in_except = True
            except_indent = indent
            except_line = i
        elif in_except:
            if indent <= except_indent and stripped and not stripped.startswith("#"):
                in_except = False
            elif indent > except_indent:
                if empty_return_pat.search(stripped):
                    # Check if there's logging before the return
                    except_body = "".join(lines[except_line:i]).lower()
                    if "log" not in except_body and "logger" not in except_body and "logging" not in except_body:
                        arch02_locs.append(f"{rpath}:{i + 1}")

make_result("ARCH-02", "warn" if arch02_locs else "pass", len(arch02_locs),
            arch02_locs[:MAX_LOCATIONS],
            f"Error-swallowing returns without logging: {len(arch02_locs)} found")


# =========================================================================
# GROUP K: Quality - Extended (added by review-optimizer 2026-03-28)
# =========================================================================

# QUAL-07: Unguarded next() without default
qual07_locs = []
next_pat = re.compile(r"\bnext\s*\([^,)]+\)")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if next_pat.search(line.strip()):
            # next() with only one arg (no default) — check it's not next(iter)
            # which is fine; we want next(x for x in ...) without default
            stripped = line.strip()
            if " for " in stripped and "," not in stripped.split("next(", 1)[-1].split(")")[0]:
                qual07_locs.append(f"{rpath}:{i + 1}")

make_result("QUAL-07", "warn" if qual07_locs else "pass", len(qual07_locs), qual07_locs,
            f"Unguarded next() without default: {len(qual07_locs)} found")

# QUAL-08: Unbounded collection growth in loops
qual08_locs = []
grow_pat = re.compile(r"\.(append|add|extend|update)\(")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    in_loop_indent = -1
    loop_line = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if re.match(r"\s*while\s+True", line):
            in_loop_indent = indent
            loop_line = i
        elif in_loop_indent >= 0:
            if indent <= in_loop_indent and stripped and not stripped.startswith("#"):
                in_loop_indent = -1
            elif indent > in_loop_indent and grow_pat.search(stripped):
                # Check if there's a size check nearby
                window = "".join(lines[max(0, i - 5):min(i + 5, len(lines))])
                if "len(" not in window and "max_" not in window and "limit" not in window:
                    qual08_locs.append(f"{rpath}:{i + 1}")

make_result("QUAL-08", "warn" if qual08_locs else "pass", len(qual08_locs),
            qual08_locs[:MAX_LOCATIONS],
            f"Unbounded collection growth in while True: {len(qual08_locs)} found")


# =========================================================================
# GROUP L: Security - Shallow Copy & Symlinks (added by review-optimizer 2026-03-28-v2)
# =========================================================================

# SEC-14: Shallow .copy() on dicts that contain password/secret/key fields
sec14_locs = []
copy_pat = re.compile(r"\.copy\(\)")
secret_field_pat = re.compile(r'["\'](password|secret|key|token|credential)["\']', re.IGNORECASE)
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if copy_pat.search(line) and "deepcopy" not in line:
            # Check surrounding 20 lines for password/secret field access
            window = "".join(lines[max(0, i - 10):min(i + 10, len(lines))])
            if secret_field_pat.search(window):
                # Check if deepcopy is used anywhere in the function
                func_start = i
                for k in range(i - 1, -1, -1):
                    if re.match(r"\s*def ", lines[k]):
                        func_start = k
                        break
                func_body = "".join(lines[func_start:min(i + 20, len(lines))])
                if "deepcopy" not in func_body:
                    sec14_locs.append(f"{rpath}:{i + 1}")

make_result("SEC-14", "warn" if sec14_locs else "pass", len(sec14_locs), sec14_locs,
            f"Shallow copy on dict with secret fields: {len(sec14_locs)} found")

# SEC-15: rglob/os.walk without symlink protection
sec15_locs = []
rglob_pat = re.compile(r"\.(rglob|glob)\(")
walk_pat = re.compile(r"os\.walk\(")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if rglob_pat.search(stripped):
            # Check if there's symlink filtering nearby
            window = "".join(lines[max(0, i - 5):min(i + 10, len(lines))])
            if "is_symlink" not in window and "follow_symlinks" not in window:
                sec15_locs.append(f"{rpath}:{i + 1}: rglob without symlink guard")
        if walk_pat.search(stripped):
            if "followlinks=False" not in stripped and "followlinks = False" not in stripped:
                # Check if followlinks is set in the full call
                full_call, _ = extract_full_call(lines, i)
                if "followlinks" not in full_call:
                    sec15_locs.append(f"{rpath}:{i + 1}: os.walk without followlinks=False")

make_result("SEC-15", "warn" if sec15_locs else "pass", len(sec15_locs), sec15_locs,
            f"Recursive traversal without symlink protection: {len(sec15_locs)} found")


# =========================================================================
# GROUP M: Architecture - Atomicity & Concurrency (added by review-optimizer 2026-03-28-v2)
# =========================================================================

# ARCH-03: Non-atomic writes on critical files (metadata, snapshot, key files)
arch03_locs = []
critical_file_pat = re.compile(r'(metadata|snapshot|encryption_key|\.json|\.key)', re.IGNORECASE)
direct_write_pat = re.compile(r'open\(.*["\']w["\']')
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if direct_write_pat.search(line):
            # Check if writing to a critical file
            window = "".join(lines[max(0, i - 5):min(i + 5, len(lines))])
            if critical_file_pat.search(window):
                # Check if there's a rename/replace pattern nearby (atomic write)
                wider_window = "".join(lines[max(0, i - 3):min(i + 20, len(lines))])
                if "os.rename" not in wider_window and ".replace(" not in wider_window and "os.replace" not in wider_window and "shutil.move" not in wider_window:
                    arch03_locs.append(f"{rpath}:{i + 1}")

make_result("ARCH-03", "warn" if arch03_locs else "pass", len(arch03_locs),
            arch03_locs[:MAX_LOCATIONS],
            f"Non-atomic writes on critical files: {len(arch03_locs)} found")

# ARCH-04: File operations without locking (snapshot JSON, symlink creation)
arch04_locs = []
symlink_pat = re.compile(r"\.symlink_to\(")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    text = "".join(lines)
    has_flock = "fcntl" in text or "flock" in text or "FileLock" in text
    if has_flock:
        continue
    for i, line in enumerate(lines):
        if symlink_pat.search(line):
            # Symlink creation without file locking
            window = "".join(lines[max(0, i - 10):i])
            if "lock" not in window.lower():
                arch04_locs.append(f"{rpath}:{i + 1}: symlink_to without lock")

make_result("ARCH-04", "info" if arch04_locs else "pass", len(arch04_locs),
            arch04_locs[:MAX_LOCATIONS],
            f"Concurrent-unsafe file operations (no locking): {len(arch04_locs)} found")


# =========================================================================
# GROUP N: Quality - Resource Handles (added by review-optimizer 2026-03-28-v2)
# =========================================================================

# QUAL-09: tar.extractfile() without context manager or close()
qual09_locs = []
extractfile_pat = re.compile(r"(\w+)\s*=\s*\w+\.extractfile\(")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        m = extractfile_pat.search(line)
        if m:
            varname = m.group(1)
            # Check if it's in a with statement
            pre_line = line.lstrip()
            if pre_line.startswith("with "):
                continue
            # Check next 15 lines for .close() call
            window = "".join(lines[i:min(i + 15, len(lines))])
            if f"{varname}.close()" not in window and "with " not in lines[i]:
                qual09_locs.append(f"{rpath}:{i + 1}")

make_result("QUAL-09", "warn" if qual09_locs else "pass", len(qual09_locs), qual09_locs,
            f"tar.extractfile() without close: {len(qual09_locs)} found")

# QUAL-10: f-string in logging calls (eager evaluation even when level suppressed)
qual10_locs = []
fstr_log_pat = re.compile(r"logging\.(debug|info|warning|error|critical)\(f[\"']")
for pyfile in find_py_files(SRC_DIR):
    lines = read_lines(pyfile)
    rpath = rel_path(pyfile)
    for i, line in enumerate(lines):
        if fstr_log_pat.search(line.strip()):
            qual10_locs.append(f"{rpath}:{i + 1}")

make_result("QUAL-10", "warn" if qual10_locs else "pass", len(qual10_locs), qual10_locs,
            f"f-string in logging (eager eval): {len(qual10_locs)} found")


# =========================================================================
# Output
# =========================================================================

print(json.dumps(results, indent=2))
PYTHON_SCRIPT

exit 0
