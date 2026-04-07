"""Claude Code page — config cleanup, sessions inspector, project history, MCP servers."""

import html as html_mod
from typing import Any

import streamlit as st

from web.cache import (
    get_binaries_stats,
    get_claude_stats,
    get_session_inventory,
    get_token_accounting,
    invalidate,
    list_session_projects,
)
from web.components.action_bar import danger_button
from web.state import AppComponents
from web.views.claude_code_cleanup import (
    render_config_tab,
    render_project_history_tab,
)


def render_claude_code(app: AppComponents) -> None:
    """Render the Claude Code page."""
    st.markdown('<div class="page-title">Claude Cleanup</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-subtitle">Inspect and manage everything Claude Code stores under ~/.claude/</div>',
        unsafe_allow_html=True,
    )

    claude_stats = get_claude_stats(app.claude_config)
    binaries_stats = get_binaries_stats(app.claude_config)

    if not claude_stats.get("exists"):
        st.warning("Claude configuration directory not found at ~/.claude/")
        return

    # Top metrics
    accounting = get_token_accounting(app.claude_config)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Size", f"{claude_stats['total_size_mb']} MB")
    with col2:
        st.metric("Projects", accounting["project_count"])
    with col3:
        st.metric("Sessions", accounting["session_count"])
    with col4:
        ratio = accounting["hidden_ratio"] * 100
        st.metric(
            "Hidden Data",
            f"{ratio:.0f}%",
            help="Subagent and memory bytes as a share of total session data",
        )

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs(["Sessions", "Config Cleanup", "Project History", "MCP Servers"])
    with tab1:
        _render_sessions_tab(app, accounting)
    with tab2:
        render_config_tab(app, claude_stats, binaries_stats)
    with tab3:
        render_project_history_tab(app)
    with tab4:
        _render_mcp_tab(app)


# ───────────────────────────────────────────────────────────────────────
# Sessions tab (read-only inspector)
# ───────────────────────────────────────────────────────────────────────


def _render_sessions_tab(app: AppComponents, accounting: dict[str, Any]) -> None:
    """Read-only inspector for Claude Code sessions, subagents, and memory."""
    st.markdown('<div class="section-header">Data Accounting</div>', unsafe_allow_html=True)

    visible_mb = accounting["visible_bytes"] / (1024 * 1024)
    hidden_mb = accounting["hidden_bytes"] / (1024 * 1024)
    multiplier = accounting["data_multiplier"]

    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    with mcol1:
        st.metric("Visible (transcripts)", f"{visible_mb:.1f} MB")
    with mcol2:
        st.metric("Hidden (subagents/memory)", f"{hidden_mb:.1f} MB")
    with mcol3:
        st.metric("Data Multiplier", f"{multiplier:.2f}x" if multiplier else "—")
    with mcol4:
        st.metric("Compactions", accounting["compaction_count"])

    st.caption(
        "Bytes on disk, not real tokens. Visible = primary session JSONL files. "
        "Hidden = subagents/, sidechains, compaction logs, and memory files."
    )

    st.markdown("---")
    st.markdown('<div class="section-header">Projects by Data Volume</div>', unsafe_allow_html=True)

    projects = list_session_projects(app.claude_config)
    if not projects:
        st.info("No projects found under ~/.claude/projects/")
        return

    top = projects[:25]
    for proj in top:
        if proj["visible_bytes"] + proj["hidden_bytes"] == 0:
            continue
        _render_project_row(app, proj)

    if len(projects) > 25:
        st.caption(f"Showing top 25 of {len(projects)} projects.")

    st.markdown("---")
    with st.expander("System Reminder Scan"):
        st.caption(
            "Counts injected `system-reminder` blocks containing the 'NEVER mention' signature "
            "across raw session JSONLs. Pure read; no files modified."
        )
        if st.button("Scan", key="cc_scan_reminders"):
            with st.spinner("Scanning..."):
                hits = app.claude_config.scan_system_reminders(limit=100)
            st.session_state["cc_reminder_hits"] = hits
        hits = st.session_state.get("cc_reminder_hits")
        if hits is not None:
            st.text(f"Found {len(hits)} occurrences (capped at 100).")
            for h in hits[:25]:
                st.markdown(
                    f'<span class="mono-text">{html_mod.escape(h["source_file"])}:{h["line_number"]}</span>',
                    unsafe_allow_html=True,
                )


def _render_project_row(app: AppComponents, proj: dict[str, Any]) -> None:
    """Render one project row with expander showing per-session breakdown."""
    label_path = proj["original_path"]
    visible_mb = proj["visible_bytes"] / (1024 * 1024)
    hidden_mb = proj["hidden_bytes"] / (1024 * 1024)
    total_mb = visible_mb + hidden_mb
    last = (proj["last_active"] or "").split("T")[0]

    header = (
        f"{label_path}  —  {total_mb:.1f} MB  "
        f"(visible {visible_mb:.1f} / hidden {hidden_mb:.1f})  "
        f"·  {proj['session_count']} sess  ·  {proj['agent_count']} agents"
    )
    if proj["compaction_count"]:
        header += f"  ·  {proj['compaction_count']} compactions"
    if last:
        header += f"  ·  {last}"

    with st.expander(header):
        inv = get_session_inventory(app.claude_config, proj["name"])
        sessions = inv["sessions"]
        if not sessions:
            st.info("No sessions in this project")
        else:
            for sess in sessions[:20]:
                vbytes_mb = sess["visible_bytes"] / (1024 * 1024)
                agents = len([a for a in sess["agents"] if not a["is_compaction"]])
                compacts = sess["compactions"]
                mtime = (sess["mtime"] or "").replace("T", " ").split(".")[0]
                st.markdown(
                    f'<span class="mono-text">{html_mod.escape(sess["session_id"][:8])}</span>'
                    f"  ·  {vbytes_mb:.2f} MB  ·  {agents} agents  ·  {compacts} compact  ·  {mtime}",
                    unsafe_allow_html=True,
                )
            if len(sessions) > 20:
                st.caption(f"Showing 20 of {len(sessions)} sessions")

        if inv["memory_files"]:
            st.markdown("**Memory files**")
            for mf in inv["memory_files"]:
                size_kb = mf["size_bytes"] / 1024
                st.markdown(
                    f'<span class="mono-text">{html_mod.escape(mf["file_name"])}</span>'
                    f"  ·  {size_kb:.1f} KB",
                    unsafe_allow_html=True,
                )


# ───────────────────────────────────────────────────────────────────────
# MCP tab (kept here, small enough)
# ───────────────────────────────────────────────────────────────────────


def _render_mcp_tab(app: AppComponents) -> None:
    """MCP server management."""
    success, servers, error = app.claude_config.get_mcp_servers()
    if not success:
        st.error(f"Error reading MCP config: {error}")
        return

    if not servers:
        st.info("No MCP servers configured")
    else:
        st.text(f"Configured servers: {len(servers)}")
        for server in servers:
            with st.expander(f"{server['name']}" + (" (disabled)" if server.get("disabled") else "")):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(
                        f'<span class="mono-text">Command: {html_mod.escape(server["command"])}</span>',
                        unsafe_allow_html=True,
                    )
                    if server.get("args"):
                        st.markdown(
                            f'<span class="mono-text">Args: {html_mod.escape(" ".join(server["args"]))}</span>',
                            unsafe_allow_html=True,
                        )
                    if server.get("env"):
                        st.text("Environment:")
                        secret_hints = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
                        for key, value in server["env"].items():
                            sval = str(value)
                            if any(hint in key.upper() for hint in secret_hints):
                                masked = sval[:4] + "***" if len(sval) > 4 else "***"
                                st.text(f"  {key}={masked}")
                            else:
                                st.text(f"  {key}={sval}")
                with col2:
                    if danger_button("Delete", key=f"mcp_del_{server['name']}"):
                        with st.spinner(f"Deleting {server['name']}..."):
                            ok, err = app.claude_config.delete_mcp_server(server["name"])
                        if ok:
                            st.success(f"Deleted {server['name']}")
                            invalidate()
                            st.rerun()
                        else:
                            st.error(err)

    st.markdown("---")
    st.markdown('<div class="section-header">Add MCP Server</div>', unsafe_allow_html=True)

    with st.form("add_mcp_server_form"):
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            server_name = st.text_input("Server Name", placeholder="my-mcp-server")
            server_command = st.text_input("Command", placeholder="node")
        with fcol2:
            server_args = st.text_input("Arguments (space-separated)", placeholder="/path/to/server.js")
            server_env = st.text_area("Environment (KEY=VALUE per line)", placeholder="API_KEY=key\nENV=prod")

        if st.form_submit_button("Add Server"):
            if server_name and server_command:
                args = server_args.split() if server_args else []
                env: dict[str, str] = {}
                if server_env:
                    for line in server_env.strip().split("\n"):
                        if "=" in line:
                            k, v = line.split("=", 1)
                            env[k.strip()] = v.strip()
                ok, err = app.claude_config.add_mcp_server(server_name, server_command, args, env)
                if ok:
                    st.success(f"Added '{server_name}'")
                    invalidate()
                    st.rerun()
                else:
                    st.error(err)
            else:
                st.error("Server name and command are required")
