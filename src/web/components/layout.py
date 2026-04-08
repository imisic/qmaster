"""Shared layout primitives for Quartermaster pages.

These helpers enforce a small visual vocabulary across every view: one page
header style, one metric grid, one action bar with a single primary action,
one item picker, one restore section, one defaults expander, one confirmation
dialog pattern, and one section break. Every view should use these instead of
hand-rolling markdown + columns.
"""

from __future__ import annotations

import html as html_mod
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Any

import streamlit as st

from web.components.status_badge import status_badge


# ── Page Header ──────────────────────────────────────────────────────


def page_header(title: str, subtitle: str | None = None) -> None:
    """Render a consistent page title and optional one-line subtitle.

    Never add a third descriptive paragraph below; if more context is needed,
    attach it as a tooltip on the title or to a specific section.
    """
    st.markdown(
        f'<div class="page-title">{html_mod.escape(title)}</div>',
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            f'<div class="page-subtitle">{html_mod.escape(subtitle)}</div>',
            unsafe_allow_html=True,
        )


# ── Section ──────────────────────────────────────────────────────────


def section(title: str) -> None:
    """Render a small uppercase section label (no divider line above)."""
    st.markdown(
        f'<div class="section-header">{html_mod.escape(title)}</div>',
        unsafe_allow_html=True,
    )


# ── Item Heading ─────────────────────────────────────────────────────


def item_heading(name: str) -> None:
    """Prominent heading for the currently-selected item on a detail page.

    Emits a real <h2> tag (so screen readers can navigate) styled larger
    and bolder than body text — sits between the page title and body in the
    type scale. Use on Projects, Databases, and any other "picker + detail"
    page so the selected item is the visual anchor.
    """
    st.markdown(
        f'<h2 class="item-heading">{html_mod.escape(name)}</h2>',
        unsafe_allow_html=True,
    )


# ── Block Heading ────────────────────────────────────────────────────


def block_heading(title: str) -> None:
    """Heading for a major content block within a page (between item_heading
    and section in weight). Use to label significant sub-areas like
    'Recent Backups', 'Backup Timeline', 'Quick Actions' — anywhere a small
    .section-header label looks too quiet for the content underneath.

    Emits a real <h3> tag for screen readers. Sits at:
        page_title (h1, 1.5rem) > item_heading (h2, 1.25rem) >
        block_heading (h3, 1rem) > section (uppercase 0.75rem label)
    """
    st.markdown(
        f'<h3 class="block-heading">{html_mod.escape(title)}</h3>',
        unsafe_allow_html=True,
    )


# ── Metrics Grid ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Metric:
    """A single cell in a metrics grid.

    status_level: optional badge level ('healthy', 'warning', 'critical',
    'inactive', 'info') - when set, the value is rendered as a colored
    status badge instead of a plain metric.
    """

    label: str
    value: Any
    status_level: str | None = None
    help: str | None = None


def metrics_grid(metrics: Sequence[Metric], *, max_columns: int = 4) -> None:
    """Render a metrics grid. Enforces at most `max_columns` columns.

    If the number of metrics is larger than max_columns, metrics wrap onto
    additional rows of the same width. A status_level on a metric renders it
    as a colored badge cell instead of a raw value.
    """
    if not metrics:
        return

    cols_per_row = min(max_columns, len(metrics))
    rows = [metrics[i : i + cols_per_row] for i in range(0, len(metrics), cols_per_row)]

    for row in rows:
        cols = st.columns(cols_per_row)
        for idx, metric in enumerate(row):
            with cols[idx]:
                if metric.status_level:
                    # Render a status cell that matches st.metric's exact
                    # padding/border/background so Health lines up with
                    # neighbouring numeric metrics. Keep the label style in
                    # sync with [data-testid="stMetricLabel"] from theme.py.
                    st.markdown(
                        f'<div class="qm-metric-status">'
                        f'<div class="qm-metric-status-label">'
                        f"{html_mod.escape(metric.label)}</div>"
                        f'<div class="qm-metric-status-value">'
                        f"{status_badge(str(metric.value), metric.status_level)}"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.metric(metric.label, metric.value, help=metric.help)
        # Pad trailing columns if the final row is short, so widths stay equal
        for idx in range(len(row), cols_per_row):
            with cols[idx]:
                st.empty()


# ── Action Bar ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Action:
    """A button in an action bar.

    on_click is called with no arguments when the button is pressed. If
    disabled is True, the button is rendered but not clickable.
    """

    label: str
    key: str
    on_click: Callable[[], None]
    disabled: bool = False
    help: str | None = None


def action_bar(primary: Action, secondary: Sequence[Action] = ()) -> None:
    """Render one primary action followed by optional secondaries.

    Enforces exactly one primary CTA per screen. The primary button is
    rendered twice as wide as each secondary so visual weight matches
    semantic weight — secondaries read as subordinate, not peer.
    """
    if secondary:
        # Primary weight = 2, each secondary weight = 1, trailing spacer = 1
        weights = [2] + [1] * len(secondary) + [1]
        cols = st.columns(weights)
    else:
        cols = st.columns([2, 4])  # primary left, breathing room right

    with cols[0]:
        if st.button(
            primary.label,
            type="primary",
            use_container_width=True,
            key=primary.key,
            disabled=primary.disabled,
            help=primary.help,
        ):
            primary.on_click()

    for idx, action in enumerate(secondary, start=1):
        with cols[idx]:
            if st.button(
                action.label,
                use_container_width=True,
                key=action.key,
                disabled=action.disabled,
                help=action.help,
            ):
                action.on_click()


# ── Item Picker ──────────────────────────────────────────────────────


def item_picker(
    label: str,
    items: Sequence[str],
    *,
    key: str,
) -> str | None:
    """Pick one item from a list.

    Uses horizontal pills (st.segmented_control) regardless of count —
    Streamlit wraps them onto multiple rows automatically. Returns the
    selected item, or None when the item list is empty.
    """
    if not items:
        return None

    return st.segmented_control(
        label,
        list(items),
        default=items[0],
        key=key,
        label_visibility="collapsed",
    )


# ── Restore Section ──────────────────────────────────────────────────


def restore_section(
    backup_names: Sequence[str],
    *,
    key_prefix: str,
    on_restore: Callable[[str], None],
) -> None:
    """Render a compact 'restore from backup' row.

    No section header — the visible label on the selectbox carries the
    context. The Restore button is rendered as a *secondary* action: each
    page already has one primary CTA (Backup Now), and the destructive
    confirmation happens in the dialog that opens on click. Keeping a
    second red primary on screen violates the one-primary-per-screen rule.
    """
    if not backup_names:
        return

    restore_col, btn_col = st.columns([4, 1])
    with restore_col:
        selected = st.selectbox(
            "Restore from backup",
            list(backup_names),
            key=f"{key_prefix}_restore_sel",
        )
    with btn_col:
        # Visual spacer so the button aligns with the selectbox control, not its label
        st.write("")
        if st.button(
            "Restore",
            use_container_width=True,
            key=f"{key_prefix}_restore_btn",
        ):
            on_restore(selected)


# ── Defaults Expander ────────────────────────────────────────────────


def defaults_expander(
    title: str,
    caption: str,
    fields: Sequence[tuple[str, Any]],
) -> None:
    """Read-only defaults expander used on Projects, Databases, Storage.

    fields is a list of (label, value) pairs. Columns are auto-balanced.
    """
    with st.expander(title, expanded=False):
        st.caption(caption)
        if not fields:
            return
        cols = st.columns(len(fields))
        for col, (label, value) in zip(cols, fields):
            with col:
                st.text(f"{label}: {value}")


# ── Confirmation Dialog ──────────────────────────────────────────────


def show_confirm(
    *,
    title: str,
    warning: str,
    confirm_label: str,
    on_confirm: Callable[[], None],
    key_prefix: str,
    info: bool = False,
) -> None:
    """Open a confirmation dialog.

    The dialog shows a warning/info banner, a confirm button (primary), and a
    cancel button. on_confirm runs inside the dialog scope; it's responsible
    for invalidating cache and rerunning.

    Use `info=True` for non-destructive confirmations (e.g. fetch, clone).
    """

    @st.dialog(title)
    def _dialog() -> None:
        if info:
            st.info(warning)
        else:
            st.warning(warning)
        col1, col2 = st.columns(2)
        with col1:
            if st.button(
                confirm_label,
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_confirm",
            ):
                on_confirm()
        with col2:
            if st.button(
                "Cancel",
                use_container_width=True,
                key=f"{key_prefix}_cancel",
            ):
                st.rerun()

    _dialog()
