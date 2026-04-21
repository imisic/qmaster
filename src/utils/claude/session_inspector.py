"""
Claude Config Manager — read-only session inspector.

Surfaces session, subagent, and memory data from ~/.claude/projects/
without modifying anything. No hooks installed, no settings touched.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Cap how much we read from any single file to keep memory bounded
MAX_BYTES_PER_FILE = 5_000_000
MAX_REMINDER_LINE = 2000
SYSTEM_REMINDER_MARKER = "system-reminder"
HIDDEN_NEVER_MENTION = "NEVER mention"


class _SessionInspectorMixin:
    """Read-only inspection of Claude Code session data. Requires _ClaudeConfigBase."""

    def list_session_projects(self) -> list[dict[str, Any]]:
        """
        List every project across all managed .claude/projects/ dirs with session counts and byte totals.

        Returns one row per project with:
            name, original_path, session_count, agent_count, compaction_count,
            visible_bytes, hidden_bytes, hidden_ratio, last_active, source
        """
        rows = []
        for projects_dir in self.all_projects_dirs:
            source_label = str(projects_dir.parent)
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                try:
                    row = self._inspect_project_dir(project_dir)
                    row["source"] = source_label
                except (OSError, PermissionError) as e:
                    logger.warning("Cannot inspect project %s: %s", project_dir.name, e)
                    continue
                rows.append(row)

        rows.sort(key=lambda r: r["hidden_bytes"] + r["visible_bytes"], reverse=True)
        return rows

    def _inspect_project_dir(self, project_dir: Path) -> dict[str, Any]:
        """Walk one project dir and tally visible vs hidden byte counts."""
        visible_bytes = 0
        hidden_bytes = 0
        session_count = 0
        agent_count = 0
        compaction_count = 0
        last_mtime = 0.0

        # Top-level *.jsonl files are the visible primary session transcripts
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                stat = jsonl.stat()
            except OSError as e:
                logger.debug("stat failed %s: %s", jsonl, e)
                continue
            visible_bytes += stat.st_size
            session_count += 1
            last_mtime = max(last_mtime, stat.st_mtime)

        # Session UUID subdirs hold subagents/ and (sometimes) memory/
        for child in project_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name == "memory":
                # Memory files belong to the project, not a single session
                hidden_bytes += self.get_directory_size(child)
                continue
            sa_dir = child / "subagents"
            if not sa_dir.exists():
                continue
            for agent_file in sa_dir.glob("*.jsonl"):
                try:
                    stat = agent_file.stat()
                except OSError as e:
                    logger.debug("stat failed %s: %s", agent_file, e)
                    continue
                hidden_bytes += stat.st_size
                last_mtime = max(last_mtime, stat.st_mtime)
                if "compact" in agent_file.name:
                    compaction_count += 1
                else:
                    agent_count += 1

        total = visible_bytes + hidden_bytes
        return {
            "name": project_dir.name,
            "original_path": self._decode_project_name(project_dir.name),
            "session_count": session_count,
            "agent_count": agent_count,
            "compaction_count": compaction_count,
            "visible_bytes": visible_bytes,
            "hidden_bytes": hidden_bytes,
            "hidden_ratio": (hidden_bytes / total) if total else 0.0,
            "last_active": datetime.fromtimestamp(last_mtime).isoformat() if last_mtime else None,
        }

    def _decode_project_name(self, encoded: str) -> str:
        """
        Resolve a ~/.claude/projects encoded name to its real path.

        Claude Code's encoding maps both '/' and '.' to '-', so the dash-only
        form is ambiguous. Resolution order:

            1. Read `cwd` from a session JSONL — authoritative.
            2. Probe the filesystem: try each '-' as '/', '-', or '.' and keep
               the branch whose directory actually exists on disk.
            3. Naive dash-to-slash as a last resort.
        """
        # Search across all projects dirs for the encoded name
        for pd in self.all_projects_dirs:
            project_dir = pd / encoded
            if project_dir.is_dir():
                cwd = self._peek_session_cwd(project_dir)
                if cwd:
                    return cwd

        probed = self._decode_by_fs_probe(encoded)
        if probed:
            return probed
        if not encoded.startswith("-"):
            return encoded
        return "/" + encoded.lstrip("-").replace("-", "/")

    def _decode_by_fs_probe(self, encoded: str) -> str | None:
        """
        Resolve an encoded project name by walking the filesystem.

        Each real path segment may be built from one or more consecutive tokens
        joined by '-' or '.'. We recursively try all groupings and keep the
        first whose directory exists on disk. `Path.exists` at every step
        prunes bad branches immediately, so worst-case cost stays small.
        """
        if not encoded.startswith("-"):
            return None
        tokens = encoded.lstrip("-").split("-")
        # Worst case is 2^(n-1) per segment; bail on pathological inputs.
        if not tokens or len(tokens) > 15:
            return None

        def search(i: int, current: str) -> str | None:
            if i == len(tokens):
                return current if Path(current).is_dir() else None
            max_k = len(tokens) - i
            for k in range(1, max_k + 1):
                for segment in _join_variants(tokens[i : i + k]):
                    cand = current + "/" + segment
                    if Path(cand).exists():
                        found = search(i + k, cand)
                        if found:
                            return found
            return None

        try:
            return search(0, "")
        except (OSError, PermissionError) as e:
            logger.debug("Probe failed for %s: %s", encoded, e)
            return None

    def _peek_session_cwd(self, project_dir: Path) -> str | None:
        """Return the cwd field from the first session JSONL that has one."""
        try:
            jsonls = list(project_dir.glob("*.jsonl"))
        except OSError as e:
            logger.debug("Cannot list jsonls in %s: %s", project_dir, e)
            return None
        for jsonl in jsonls:
            try:
                with jsonl.open(errors="replace") as fh:
                    for _ in range(20):
                        line = fh.readline()
                        if not line:
                            break
                        line = line.strip()
                        if not line or "cwd" not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cwd = rec.get("cwd")
                        if isinstance(cwd, str) and cwd:
                            return cwd
            except (OSError, PermissionError) as e:
                logger.debug("Cannot peek %s: %s", jsonl, e)
                continue
        return None

    def get_session_inventory(self, project_name: str) -> dict[str, Any]:
        """
        Inventory every session inside one project.

        Returns:
            dict with 'sessions' (list) and 'memory_files' (list).
            Each session row carries session_id, mtime, visible_bytes, agents (list).
        """
        project_dir = self.projects_dir / project_name
        if not project_dir.is_dir():
            return {"sessions": [], "memory_files": []}

        seen: dict[str, dict[str, Any]] = {}

        for jsonl in project_dir.glob("*.jsonl"):
            session_id = jsonl.stem
            try:
                stat = jsonl.stat()
            except OSError as e:
                logger.debug("stat failed %s: %s", jsonl, e)
                continue
            seen[session_id] = {
                "session_id": session_id,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "visible_bytes": stat.st_size,
                "agents": [],
                "compactions": 0,
            }

        for child in project_dir.iterdir():
            if not child.is_dir() or child.name == "memory":
                continue
            session_id = child.name
            row = seen.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "mtime": None,
                    "visible_bytes": 0,
                    "agents": [],
                    "compactions": 0,
                },
            )
            sa_dir = child / "subagents"
            if not sa_dir.exists():
                continue
            for agent_file in sorted(sa_dir.glob("*.jsonl")):
                try:
                    stat = agent_file.stat()
                except OSError as e:
                    logger.debug("stat failed %s: %s", agent_file, e)
                    continue
                if "compact" in agent_file.name:
                    row["compactions"] += 1
                row["agents"].append(
                    {
                        "file_name": agent_file.name,
                        "size_bytes": stat.st_size,
                        "is_compaction": "compact" in agent_file.name,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )

        sessions = sorted(seen.values(), key=lambda r: r.get("mtime") or "", reverse=True)

        memory_files = []
        memory_dir = project_dir / "memory"
        if memory_dir.exists():
            for mf in sorted(memory_dir.glob("*")):
                if not mf.is_file():
                    continue
                try:
                    stat = mf.stat()
                    with mf.open("r", errors="replace") as fh:
                        content = fh.read(5000)
                except OSError as e:
                    logger.warning("Cannot read memory file %s: %s", mf, e)
                    continue
                memory_files.append(
                    {
                        "file_name": mf.name,
                        "size_bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "content": content,
                    }
                )

        return {"sessions": sessions, "memory_files": memory_files}

    def get_token_accounting(self) -> dict[str, Any]:
        """
        Aggregate visible vs hidden byte counts across all projects.

        These are *byte counts* of session/agent JSONL files, not real tokens.
        Useful as a proxy for context budget consumption.
        """
        projects = self.list_session_projects()
        visible = sum(p["visible_bytes"] for p in projects)
        hidden = sum(p["hidden_bytes"] for p in projects)
        total = visible + hidden
        return {
            "visible_bytes": visible,
            "hidden_bytes": hidden,
            "total_bytes": total,
            "hidden_ratio": (hidden / total) if total else 0.0,
            "data_multiplier": (total / visible) if visible else 0.0,
            "project_count": len(projects),
            "session_count": sum(p["session_count"] for p in projects),
            "agent_count": sum(p["agent_count"] for p in projects),
            "compaction_count": sum(p["compaction_count"] for p in projects),
        }

    def scan_system_reminders(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Find injected system reminders in raw session JSONLs across all managed dirs.

        Looks for the literal "system-reminder" marker plus the "NEVER mention"
        signature so we don't false-positive on user messages that mention the term.
        """
        results: list[dict[str, Any]] = []
        for projects_dir in self.all_projects_dirs:
            if len(results) >= limit:
                break
            for jsonl in projects_dir.glob("*/*.jsonl"):
                if len(results) >= limit:
                    break
                try:
                    if jsonl.stat().st_size > MAX_BYTES_PER_FILE:
                        continue
                    with jsonl.open(errors="replace") as fh:
                        for i, line in enumerate(fh, start=1):
                            if SYSTEM_REMINDER_MARKER in line and HIDDEN_NEVER_MENTION in line:
                                results.append(
                                    {
                                        "source_file": str(jsonl.relative_to(projects_dir)),
                                        "line_number": i,
                                        "snippet": line.strip()[:MAX_REMINDER_LINE],
                                    }
                                )
                                if len(results) >= limit:
                                    break
                except (OSError, PermissionError) as e:
                    logger.warning("Cannot scan %s: %s", jsonl, e)
                    continue
        return results

    def read_session_messages(self, project_name: str, session_id: str, max_messages: int = 200) -> list[dict[str, Any]]:
        """
        Parse a single session JSONL into structured messages.

        Returns role, brief content, and timestamp where available.
        """
        jsonl = self.projects_dir / project_name / f"{session_id}.jsonl"
        if not jsonl.exists():
            return []
        messages: list[dict[str, Any]] = []
        try:
            with jsonl.open(errors="replace") as fh:
                for line in fh:
                    if len(messages) >= max_messages:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    messages.append(_normalize_message(msg))
        except (OSError, PermissionError) as e:
            logger.warning("Cannot read session %s: %s", jsonl, e)
        return messages


def _join_variants(parts: list[str]) -> list[str]:
    """All 2^(n-1) ways to join tokens with '-' or '.' for a single path segment."""
    if len(parts) == 1:
        return [parts[0]]
    tail_variants = _join_variants(parts[1:])
    out: list[str] = []
    for sub in tail_variants:
        out.append(parts[0] + "-" + sub)
        out.append(parts[0] + "." + sub)
    return out


def _normalize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a JSONL message to {role, text, type}."""
    role = msg.get("role") or msg.get("type") or "?"
    content = msg.get("content") or msg.get("text") or ""
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c.get("content", ""))))
            else:
                parts.append(str(c))
        content = " ".join(p for p in parts if p)
    return {"role": role, "type": msg.get("type", ""), "text": str(content)[:3000]}
