"""Reusable action button components."""

import streamlit as st


def danger_button(label: str, key: str, disabled: bool = False, help: str | None = None) -> bool:  # noqa: A002
    """Render a single danger-styled button.

    Returns:
        True if clicked.
    """
    st.markdown('<div class="danger-btn">', unsafe_allow_html=True)
    clicked = st.button(label, key=key, use_container_width=True, disabled=disabled, help=help)
    st.markdown("</div>", unsafe_allow_html=True)
    return clicked
