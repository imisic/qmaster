"""Design system and CSS theme for Quartermaster"""

import streamlit as st

# Color palette
COLORS = {
    "bg_page": "#0e1117",
    "bg_card": "#1a1f2e",
    "bg_hover": "#262d3d",
    "border": "#2d3548",
    "text_heading": "#fafafa",
    "text_body": "#a1a7b5",
    "text_muted": "#6b7280",
    "accent_blue": "#3b82f6",
    "accent_green": "#22c55e",
    "accent_amber": "#f59e0b",
    "accent_red": "#ef4444",
    "accent_purple": "#8b5cf6",
}

# Status level mapping
STATUS_COLORS = {
    "healthy": COLORS["accent_green"],
    "success": COLORS["accent_green"],
    "warning": COLORS["accent_amber"],
    "critical": COLORS["accent_red"],
    "error": COLORS["accent_red"],
    "running": COLORS["accent_blue"],
    "info": COLORS["accent_blue"],
    "inactive": COLORS["text_muted"],
}


def apply_theme() -> None:
    """Inject the full CSS design system into the Streamlit app."""
    st.markdown(_get_css(), unsafe_allow_html=True)


def _get_css() -> str:
    return f"""<style>
/* ── Global ──────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap');

section[data-testid="stSidebar"] {{
    background-color: {COLORS["bg_card"]};
    border-right: 1px solid {COLORS["border"]};
}}

/* Sidebar section headers */
.sidebar-section-header {{
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {COLORS["text_muted"]};
    padding: 0.75rem 0 0.25rem 0;
    margin-top: 0.5rem;
}}

.sidebar-divider {{
    border: none;
    border-top: 1px solid {COLORS["border"]};
    margin: 0.5rem 0;
}}

/* ── Typography ──────────────────────────────────────── */
.page-title {{
    font-size: 1.75rem;
    font-weight: 700;
    color: {COLORS["text_heading"]};
    margin-bottom: 0.25rem;
    line-height: 1.2;
}}

.page-subtitle {{
    font-size: 0.9rem;
    color: {COLORS["text_muted"]};
    margin-bottom: 1.5rem;
}}

.section-header {{
    font-size: 1.25rem;
    font-weight: 600;
    color: {COLORS["text_heading"]};
    margin-bottom: 0.75rem;
}}

.card-title {{
    font-size: 1rem;
    font-weight: 600;
    color: {COLORS["text_heading"]};
}}

.mono-text {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: {COLORS["text_body"]};
}}

/* ── Status Badges ───────────────────────────────────── */
.status-badge {{
    display: inline-block;
    padding: 0.15rem 0.6rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 1.4;
    white-space: nowrap;
}}

.status-healthy {{
    background: rgba(34, 197, 94, 0.15);
    color: {COLORS["accent_green"]};
    border: 1px solid rgba(34, 197, 94, 0.3);
}}

.status-success {{
    background: rgba(34, 197, 94, 0.15);
    color: {COLORS["accent_green"]};
    border: 1px solid rgba(34, 197, 94, 0.3);
}}

.status-warning {{
    background: rgba(245, 158, 11, 0.15);
    color: {COLORS["accent_amber"]};
    border: 1px solid rgba(245, 158, 11, 0.3);
}}

.status-critical {{
    background: rgba(239, 68, 68, 0.15);
    color: {COLORS["accent_red"]};
    border: 1px solid rgba(239, 68, 68, 0.3);
}}

.status-error {{
    background: rgba(239, 68, 68, 0.15);
    color: {COLORS["accent_red"]};
    border: 1px solid rgba(239, 68, 68, 0.3);
}}

.status-running {{
    background: rgba(59, 130, 246, 0.15);
    color: {COLORS["accent_blue"]};
    border: 1px solid rgba(59, 130, 246, 0.3);
}}

.status-info {{
    background: rgba(59, 130, 246, 0.15);
    color: {COLORS["accent_blue"]};
    border: 1px solid rgba(59, 130, 246, 0.3);
}}

.status-inactive {{
    background: rgba(107, 114, 128, 0.15);
    color: {COLORS["text_muted"]};
    border: 1px solid rgba(107, 114, 128, 0.3);
}}

/* ── Type Badges ─────────────────────────────────────── */
.type-badge {{
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}

.type-project {{
    background: rgba(59, 130, 246, 0.15);
    color: {COLORS["accent_blue"]};
}}

.type-database {{
    background: rgba(139, 92, 246, 0.15);
    color: {COLORS["accent_purple"]};
}}

.type-full {{
    background: rgba(59, 130, 246, 0.12);
    color: {COLORS["accent_blue"]};
}}

.type-incremental {{
    background: rgba(34, 197, 94, 0.12);
    color: {COLORS["accent_green"]};
}}

.type-complete {{
    background: rgba(139, 92, 246, 0.12);
    color: {COLORS["accent_purple"]};
}}

/* ── Cards ───────────────────────────────────────────── */
.qm-card {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 1.25rem;
    margin-bottom: 0.75rem;
}}

.qm-card-muted {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 1rem;
    opacity: 0.7;
}}

/* ── Metric Cards ────────────────────────────────────── */
[data-testid="stMetric"] {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 0.75rem 1rem;
}}

[data-testid="stMetricLabel"] {{
    font-size: 0.8rem !important;
    color: {COLORS["text_muted"]} !important;
}}

[data-testid="stMetricValue"] {{
    font-size: 1.4rem !important;
    font-weight: 700 !important;
}}

/* ── Danger Buttons ──────────────────────────────────── */
.danger-btn button {{
    background-color: {COLORS["accent_red"]} !important;
    color: white !important;
    border: none !important;
}}

.danger-btn button:hover {{
    background-color: #dc2626 !important;
}}

/* ── Empty State ─────────────────────────────────────── */
.empty-state {{
    text-align: center;
    padding: 3rem 1rem;
    color: {COLORS["text_muted"]};
}}

.empty-state-icon {{
    font-size: 2.5rem;
    margin-bottom: 0.75rem;
    opacity: 0.5;
}}

.empty-state-title {{
    font-size: 1.1rem;
    font-weight: 600;
    color: {COLORS["text_body"]};
    margin-bottom: 0.25rem;
}}

.empty-state-description {{
    font-size: 0.85rem;
    color: {COLORS["text_muted"]};
    margin-bottom: 1rem;
}}

/* ── Health Alert Banner ─────────────────────────────── */
.health-alert {{
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.3);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    color: {COLORS["accent_amber"]};
    font-size: 0.85rem;
    margin-bottom: 1rem;
}}

.health-alert-critical {{
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: {COLORS["accent_red"]};
}}

/* ── Cleanup Table Row ───────────────────────────────── */
.cleanup-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid {COLORS["border"]};
    font-size: 0.85rem;
}}

.cleanup-row:last-child {{
    border-bottom: none;
}}

.cleanup-row-label {{
    flex: 2;
    color: {COLORS["text_body"]};
}}

.cleanup-row-size {{
    flex: 1;
    text-align: right;
    color: {COLORS["text_heading"]};
    font-weight: 500;
}}

/* ── Task Progress ───────────────────────────────────── */
.task-row {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 0.6rem 1rem;
    margin-bottom: 0.5rem;
}}

/* ── Dialog Overrides ────────────────────────────────── */
[data-testid="stDialog"] {{
    background: {COLORS["bg_card"]};
}}

/* ── Stale Row Highlight ─────────────────────────────── */
.stale-row {{
    background: rgba(245, 158, 11, 0.06);
}}

/* ── Project Path ────────────────────────────────────── */
.project-path {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: {COLORS["text_body"]};
    background: {COLORS["bg_hover"]};
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
}}

/* ── Sidebar Brand ───────────────────────────────────── */
.sidebar-brand {{
    font-size: 1.1rem;
    font-weight: 700;
    color: {COLORS["text_heading"]};
    padding: 0.25rem 0 1rem 0;
}}

/* ── Button aligned with labeled inputs ───────────────── */
/* Use class="btn-align" on a div wrapping a button column
   to push it down to align with selectbox/number_input siblings */
.btn-align {{
    padding-top: 1.65rem;
}}

/* ── Hide default Streamlit chrome ────────────────────── */
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
</style>"""
