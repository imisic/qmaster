"""Empty-state displays with call-to-action."""

import streamlit as st


def empty_state(title: str, description: str = "", icon: str = "") -> None:
    """Render a centered empty-state placeholder.

    Args:
        title: Main heading text.
        description: Subtitle/explanation.
        icon: Optional text/icon shown above the title (plain text, no emoji).
    """
    # Use flat, non-nested elements — st.markdown can strip nested divs.
    if icon:
        st.markdown(
            f'<div style="text-align:center;font-size:2.5rem;opacity:0.5;margin-bottom:0.75rem">{icon}</div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div style="text-align:center;padding:2rem 1rem 0.25rem;font-size:1.1rem;font-weight:600;color:#a1a7b5">{title}</div>',
        unsafe_allow_html=True,
    )
    if description:
        st.markdown(
            f'<div style="text-align:center;font-size:0.85rem;color:#6b7280;padding-bottom:1rem">{description}</div>',
            unsafe_allow_html=True,
        )
