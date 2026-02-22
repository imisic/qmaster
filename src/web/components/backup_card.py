"""Backup item display components."""

import streamlit as st

from utils.background_backup import BackupTask
from web.components.status_badge import task_status_badge


def task_status_row(task: BackupTask) -> None:
    """Render a background-task row with badge and progress.

    Args:
        task: BackupTask dataclass instance.
    """
    col1, col2, col3, col4 = st.columns([3, 1, 1, 2])

    with col1:
        task_name = (
            f"{task.task_type}: {task.target}" if task.target != "all" else task.task_type.replace("-", " ").title()
        )
        st.markdown(f"**{task_name}**")

    with col2:
        st.markdown(task_status_badge(task.status.value), unsafe_allow_html=True)

    with col3:
        if task.started_at:
            st.text(task.started_at.strftime("%H:%M:%S"))

    with col4:
        if task.result_message:
            st.text(task.result_message[:50])
        elif task.error_message:
            st.text(f"Error: {task.error_message[:40]}")
