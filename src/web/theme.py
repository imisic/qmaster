"""Design system and CSS theme for Quartermaster"""

import streamlit as st

# Color palette (harnesster-influenced GitHub dark, with qmaster purple preserved)
COLORS = {
    "bg_page": "#0d1117",
    "bg_card": "#161b22",
    "bg_hover": "#1c2128",
    "border": "#30363d",
    "text_heading": "#f0f6fc",
    "text_body": "#c9d1d9",
    "text_muted": "#8b949e",
    "accent_blue": "#58a6ff",
    "accent_green": "#3fb950",
    "accent_amber": "#d29922",
    "accent_red": "#f85149",
    "accent_purple": "#8b5cf6",
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
    font-size: 1.5rem;
    font-weight: 700;
    color: {COLORS["text_heading"]};
    margin-bottom: 0.25rem;
    line-height: 1.2;
}}

.page-subtitle {{
    font-size: 0.85rem;
    color: {COLORS["text_muted"]};
    margin-bottom: 1.25rem;
}}

.section-header {{
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: {COLORS["text_muted"]};
    margin: 1.5rem 0 0.5rem 0;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid {COLORS["border"]};
}}

.card-title {{
    font-size: 0.95rem;
    font-weight: 600;
    color: {COLORS["text_heading"]};
}}

/* Prominent heading for the currently-selected item on a detail page.
   Emitted as a real <h2> so screen readers get proper landmarks. Sits
   between .page-title (1.5rem/700) and body text (1rem/400) in the scale. */
.item-heading {{
    font-size: 1.25rem;
    font-weight: 700;
    color: {COLORS["text_heading"]};
    margin: 0.25rem 0 0.5rem 0;
    padding: 0;
    line-height: 1.3;
    letter-spacing: -0.01em;
}}

/* Heading for a major content block within a page. Real <h3> tag.
   Sits between .item-heading and .section-header in the type scale —
   use to label significant sub-areas without resorting to the small
   muted .section-header label that gets visually lost. */
.block-heading {{
    font-size: 1rem;
    font-weight: 600;
    color: {COLORS["text_heading"]};
    margin: 1rem 0 0.5rem 0;
    padding: 0;
    line-height: 1.3;
    letter-spacing: 0;
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
    background: rgba(63, 185, 80, 0.15);
    color: {COLORS["accent_green"]};
    border: 1px solid rgba(63, 185, 80, 0.3);
}}

.status-success {{
    background: rgba(63, 185, 80, 0.15);
    color: {COLORS["accent_green"]};
    border: 1px solid rgba(63, 185, 80, 0.3);
}}

.status-warning {{
    background: rgba(210, 153, 34, 0.15);
    color: {COLORS["accent_amber"]};
    border: 1px solid rgba(210, 153, 34, 0.3);
}}

.status-critical {{
    background: rgba(248, 81, 73, 0.15);
    color: {COLORS["accent_red"]};
    border: 1px solid rgba(248, 81, 73, 0.3);
}}

.status-error {{
    background: rgba(248, 81, 73, 0.15);
    color: {COLORS["accent_red"]};
    border: 1px solid rgba(248, 81, 73, 0.3);
}}

.status-running {{
    background: rgba(88, 166, 255, 0.15);
    color: {COLORS["accent_blue"]};
    border: 1px solid rgba(88, 166, 255, 0.3);
}}

.status-info {{
    background: rgba(88, 166, 255, 0.15);
    color: {COLORS["accent_blue"]};
    border: 1px solid rgba(88, 166, 255, 0.3);
}}

.status-inactive {{
    background: rgba(139, 148, 158, 0.15);
    color: {COLORS["text_muted"]};
    border: 1px solid rgba(139, 148, 158, 0.3);
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
    background: rgba(88, 166, 255, 0.15);
    color: {COLORS["accent_blue"]};
}}

.type-database {{
    background: rgba(139, 92, 246, 0.15);
    color: {COLORS["accent_purple"]};
}}

.type-full {{
    background: rgba(88, 166, 255, 0.12);
    color: {COLORS["accent_blue"]};
}}

.type-incremental {{
    background: rgba(63, 185, 80, 0.12);
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
    border-radius: 6px;
    padding: 0.875rem;
    margin-bottom: 0.5rem;
}}

.qm-card-muted {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 0.875rem;
    opacity: 0.7;
}}

/* ── Metric Cards ────────────────────────────────────── */
[data-testid="stMetric"] {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 0.625rem 0.875rem;
}}

[data-testid="stMetricLabel"] {{
    font-size: 0.8rem !important;
    color: {COLORS["text_muted"]} !important;
}}

[data-testid="stMetricValue"] {{
    font-size: 1.4rem !important;
    font-weight: 700 !important;
}}

/* Status-level metric cell — must match [data-testid="stMetric"] exactly
   so a Health cell lines up next to numeric metrics in the same row. */
.qm-metric-status {{
    background: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 0.625rem 0.875rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
}}

.qm-metric-status-label {{
    font-size: 0.8rem;
    color: {COLORS["text_muted"]};
    line-height: 1.4;
}}

.qm-metric-status-value {{
    display: flex;
    align-items: center;
    min-height: 1.96rem; /* ~1.4rem * 1.4 line-height of st.metric value */
}}

/* ── Danger Buttons ──────────────────────────────────── */
.danger-btn button {{
    background-color: {COLORS["accent_red"]} !important;
    color: white !important;
    border: none !important;
}}

.danger-btn button:hover {{
    background-color: #da3633 !important;
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
    background: rgba(210, 153, 34, 0.1);
    border: 1px solid rgba(210, 153, 34, 0.3);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    color: {COLORS["accent_amber"]};
    font-size: 0.85rem;
    margin-bottom: 1rem;
}}

.health-alert-critical {{
    background: rgba(248, 81, 73, 0.1);
    border: 1px solid rgba(248, 81, 73, 0.3);
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
    background: rgba(210, 153, 34, 0.06);
}}

/* ── Project Path ────────────────────────────────────── */
.project-path {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: {COLORS["text_body"]};
    background: {COLORS["bg_hover"]};
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    word-break: break-all;
}}

/* Full-width mono path line that wraps on narrow viewports instead
   of being clipped. Use for storage paths, log file paths, etc. */
.mono-path {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: {COLORS["text_muted"]};
    background: {COLORS["bg_hover"]};
    padding: 0.35rem 0.6rem;
    border-radius: 4px;
    word-break: break-all;
    margin: 0.25rem 0 0.75rem 0;
}}

/* ── Sidebar Brand ───────────────────────────────────── */
.sidebar-brand {{
    font-size: 1.1rem;
    font-weight: 700;
    color: {COLORS["text_heading"]};
    padding: 0.25rem 0 1rem 0;
}}

/* ── Hide default Streamlit chrome ────────────────────── */
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
</style>"""
