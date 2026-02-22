"""HTML Cleaner view for the Quartermaster dashboard."""

import streamlit as st

from utils.html_cleaner import HtmlCleaner

_cleaner = HtmlCleaner()

_MODES = {
    "Markdown": ("markdown", _cleaner.to_markdown),
    "Structural HTML": ("html", _cleaner.to_structural),
    "Minimal HTML": ("html", _cleaner.to_minimal),
    "Text Only": ("text", _cleaner.to_text),
}

_MAX_UPLOAD_MB = 5


def _read_uploaded_file(uploaded) -> str | None:
    """Read uploaded file with encoding fallback."""
    if uploaded.size > _MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"File exceeds {_MAX_UPLOAD_MB}MB limit.")
        return None
    raw = uploaded.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    st.error("Could not decode file. Ensure it's a text-based HTML file.")
    return None


def render_html_cleaner(app=None):
    """Render the HTML Cleaner page."""
    st.title("HTML Cleaner")
    st.caption("Clean and convert HTML content")

    # ── Input source ────────────────────────────────────────────────
    input_method = st.radio(
        "Input method",
        ["Paste HTML", "Upload File"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if input_method == "Paste HTML":
        html_input = st.text_area(
            "Paste HTML",
            height=250,
            key="html_cleaner_paste",
            label_visibility="collapsed",
            placeholder="Paste your HTML here...",
        )
    else:
        uploaded = st.file_uploader(
            "Upload HTML file",
            type=["html", "htm", "txt"],
            label_visibility="collapsed",
        )
        html_input = _read_uploaded_file(uploaded) if uploaded else ""

    # Store input in session state; clear output on change
    prev = st.session_state.get("html_cleaner_input", "")
    st.session_state["html_cleaner_input"] = html_input or ""
    if html_input != prev:
        st.session_state.pop("html_cleaner_output", None)
        st.session_state.pop("html_cleaner_mode", None)

    # ── Mode buttons ────────────────────────────────────────────────
    st.markdown("---")
    active_mode = st.session_state.get("html_cleaner_mode")
    cols = st.columns(len(_MODES))

    for col, mode_name in zip(cols, _MODES):
        with col:
            btn_type = "primary" if mode_name == active_mode else "secondary"
            if st.button(mode_name, use_container_width=True, type=btn_type):
                src = st.session_state.get("html_cleaner_input", "").strip()
                if not src:
                    st.session_state.pop("html_cleaner_output", None)
                    st.session_state.pop("html_cleaner_mode", None)
                    st.warning("Paste or upload HTML first.")
                    st.stop()
                lang, fn = _MODES[mode_name]
                st.session_state["html_cleaner_output"] = fn(src)
                st.session_state["html_cleaner_mode"] = mode_name
                st.rerun()

    # ── Output ──────────────────────────────────────────────────────
    output = st.session_state.get("html_cleaner_output")
    if output is not None:
        mode_label = st.session_state.get("html_cleaner_mode", "Result")
        lang = _MODES.get(mode_label, ("text",))[0]
        lines = output.count("\n") + 1
        chars = len(output)
        st.markdown(f"**{mode_label}** — {chars:,} chars | {lines:,} lines")
        st.code(output, language=lang)
