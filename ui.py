"""ui.py - Chat-style Streamlit UI for the Text-to-SQL Chatbot with RAG."""

import logging

import pandas as pd
import requests
import streamlit as st

from chart_utils import choose_chart

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE_URL = "http://127.0.0.1:8000"

EXAMPLE_QUESTIONS = [
    "How many sales were made?",
    "What are the best-selling products?",
    "Show total revenue by month",
    "List all active employees",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config  (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ask Your Data",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def check_api_health() -> bool:
    """Return True when the FastAPI server is reachable."""
    try:
        r = requests.get(f"{API_BASE_URL}/", timeout=5)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def call_api(question: str) -> dict:
    """POST /query and return the JSON response dict.

    On any network or HTTP error returns {"success": False, "error": <msg>}.
    """
    try:
        r = requests.post(
            f"{API_BASE_URL}/query",
            json={"question": question, "show_sql": True},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.error("API call failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar() -> str | None:
    """Render the sidebar and return an example question if one was clicked."""
    with st.sidebar:
        st.title("Ask Your Data")
        st.caption("Text-to-SQL chatbot powered by RAG")

        st.divider()

        # API status
        if check_api_health():
            st.success("✅ API connected")
        else:
            st.error("❌ API offline")
            st.info("Start the API with:\n```\nuv run uvicorn api:app\n```")

        st.divider()

        # Clear chat
        if st.button("🗑️ Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.markdown("**💡 Example questions**")

        clicked = None
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, use_container_width=True, key=f"ex_{q}"):
                clicked = q

    return clicked


# ---------------------------------------------------------------------------
# Chat rendering
# ---------------------------------------------------------------------------
def render_answer(result: dict) -> None:
    """Render one assistant answer inside an already-opened chat_message block."""
    if not result.get("success"):
        st.warning("I couldn't answer that. Try rephrasing your question.")
        return

    rows = result.get("rows") or []
    cols = result.get("cols") or []
    sql  = result.get("sql", "")
    latency  = result.get("latency_seconds", 0.0)
    attempts = result.get("attempts", 1)

    # Results table
    if rows:
        df = pd.DataFrame(rows, columns=cols if cols else None)
        st.dataframe(df, use_container_width=True)

        # Optional chart
        chart_spec = choose_chart(cols, rows)
        if chart_spec["type"] == "bar":
            chart_df = df[[chart_spec["x"], chart_spec["y"]]].set_index(chart_spec["x"])
            st.bar_chart(chart_df)
        elif chart_spec["type"] == "line":
            chart_df = df[[chart_spec["x"], chart_spec["y"]]].set_index(chart_spec["x"])
            st.line_chart(chart_df)
    else:
        st.info("No rows returned.")

    # Collapsible SQL
    if sql:
        with st.expander("SQL"):
            st.code(sql, language="sql")

    # Latency / attempts
    st.caption(f"Took {latency:.1f}s · {attempts} attempt{'s' if attempts != 1 else ''}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Entry point for the Streamlit app."""
    # Initialise message history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Sidebar (may return a clicked example question)
    example_clicked = render_sidebar()

    st.header("🤖 Ask Your Data")

    # Replay full chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                render_answer(msg["result"])

    # Determine the question for this run
    user_input = st.chat_input("Ask a question about your data…")
    question = example_clicked or user_input

    if question:
        # Show user bubble
        with st.chat_message("user"):
            st.markdown(question)
        st.session_state.messages.append({"role": "user", "content": question})

        # Fetch answer
        with st.chat_message("assistant"):
            with st.spinner("Querying…"):
                result = call_api(question)
            render_answer(result)

        st.session_state.messages.append({"role": "assistant", "result": result})


if __name__ == "__main__":
    main()
