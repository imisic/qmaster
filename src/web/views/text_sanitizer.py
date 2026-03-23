"""Text Sanitizer view for the Quartermaster dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from utils.text_sanitizer import TextSanitizer, PHONENUMBERS_AVAILABLE

if TYPE_CHECKING:
    from web.state import AppComponents

_sanitizer = TextSanitizer()


def render_text_sanitizer(app: AppComponents | None = None) -> None:
    """Render the Text Sanitizer page."""
    st.title("Text Sanitizer")
    st.caption("Strip PII from text before sharing with AI, then restore it after")

    if not PHONENUMBERS_AVAILABLE:
        st.warning(
            "Phone detection unavailable. Install `phonenumbers`: "
            "`pip install phonenumbers`"
        )

    # ── Mode toggle ──────────────────────────────────────────────
    mode = st.radio(
        "Mode",
        ["Sanitize", "Unsanitize", "Lookup"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if mode == "Sanitize":
        _render_sanitize()
    elif mode == "Unsanitize":
        _render_unsanitize()
    else:
        _render_lookup()

    # ── Mappings summary ─────────────────────────────────────────
    with st.expander("Current mappings"):
        mappings = _sanitizer.get_all_mappings()
        total = sum(len(v) for v in mappings.values())
        if total == 0:
            st.info("No mappings yet. Sanitize some text to populate.")
        else:
            for category, entries in mappings.items():
                if entries:
                    st.markdown(f"**{category.title()}** ({len(entries)} entries)")
                    for original, token in entries.items():
                        st.text(f"  {original} → [{token}]")


def _render_sanitize() -> None:
    """Sanitize mode: input text → sanitized output."""
    clean_boilerplate = st.checkbox("Clean boilerplate", value=True)

    text_input = st.text_area(
        "Paste text to sanitize",
        height=250,
        key="sanitizer_input",
        placeholder="Paste email thread, log snippet, or any text with PII...",
    )

    if st.button("Sanitize", type="primary", use_container_width=True):
        if not text_input.strip():
            st.warning("Paste some text first.")
            return

        sanitized, stats = _sanitizer.sanitize(
            text_input, clean_boilerplate=clean_boilerplate,
        )
        st.session_state["sanitizer_output"] = sanitized
        st.session_state["sanitizer_stats"] = stats
        st.rerun()

    output = st.session_state.get("sanitizer_output")
    if output is not None:
        stats = st.session_state.get("sanitizer_stats", {})
        parts = []
        if stats.get("emails_replaced"):
            parts.append(f"{stats['emails_replaced']} emails")
        if stats.get("phones_replaced"):
            parts.append(f"{stats['phones_replaced']} phones")
        if stats.get("ips_replaced"):
            parts.append(f"{stats['ips_replaced']} IPs")
        summary = ", ".join(parts) if parts else "no PII found"
        st.markdown(f"**Replaced:** {summary}")
        st.code(output, language="text")


def _render_unsanitize() -> None:
    """Unsanitize mode: AI response with tokens → restored text."""
    text_input = st.text_area(
        "Paste AI response with tokens",
        height=250,
        key="unsanitizer_input",
        placeholder="Paste text containing [EMAIL-xxxx], [PHONE-xxxx], [IP-xxxx] tokens...",
    )

    if st.button("Unsanitize", type="primary", use_container_width=True):
        if not text_input.strip():
            st.warning("Paste some text first.")
            return

        restored = _sanitizer.unsanitize(text_input)
        st.session_state["unsanitizer_output"] = restored
        st.rerun()

    output = st.session_state.get("unsanitizer_output")
    if output is not None:
        st.markdown("**Restored text:**")
        st.code(output, language="text")


def _render_lookup() -> None:
    """Lookup mode: paste a token, see the original value."""
    token = st.text_input(
        "Token to look up",
        placeholder="[EMAIL-a1b2] or PHONE-c3d4",
        key="sanitizer_lookup_input",
    )

    if st.button("Lookup", type="primary"):
        if not token.strip():
            st.warning("Enter a token.")
            return

        result = _sanitizer.lookup(token.strip())
        if result:
            st.success(f"{token.strip()} → {result}")
        else:
            st.error(f"Token not found: {token.strip()}")
