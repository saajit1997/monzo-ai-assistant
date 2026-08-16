"""Phase 7: the Monzo AI Knowledge Assistant chat app.

Retrieval (Phase 6) -> grounded generation (Phase 7) -> every exchange
logged (Phase 8 groundwork) for later product analytics.

Run with:
    streamlit run app/streamlit_app.py

Requires ANTHROPIC_API_KEY in a local .env file (see .env.example) --
unlike every earlier phase, this one costs money per query (Claude API
calls) and needs your own key. The retrieval-only test tool
(scripts/serve_search_ui.py) has no such requirement.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from monzo_ai.assistant.generate import generate_answer
from monzo_ai.assistant.query_log import log_query
from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.retrieval.index import load_index
from monzo_ai.retrieval.search import search

load_dotenv()

st.set_page_config(page_title="Monzo AI Knowledge Assistant", page_icon="💬")

# Colors below are real, pulled from data/raw/pages/*.html (the actual
# fetched Monzo pages) by counting hex codes across ~60 pages -- #FF4F40
# ("Hot Coral") was overwhelmingly the most common, with #03363B/#112231 as
# the dark ink colors and #EBEBEB as a neutral surface. Third-party colors
# that also showed up (Google's G-logo palette, a Trustpilot widget green)
# were excluded. No font files were fetchable from the raw HTML -- fonts
# load via external CSS bundles we don't have -- so this uses Poppins, a
# freely-licensed rounded sans-serif that's visually close to Monzo's own
# geometric brand typeface, not a claim to the real one.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@600;700&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: #112231; }
    h1, h2, h3 { font-family: 'Poppins', sans-serif; color: #112231; }

    [data-testid="stChatMessage"] { border-radius: 20px; padding: 0.75rem 1rem; margin-bottom: 0.5rem; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { background-color: #F5F1EC; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) { background-color: #FFFFFF; border: 1px solid #EBEBEB; }

    [data-testid="stChatInput"] textarea { border-radius: 16px; }

    .monzo-pill {
        display: inline-block; font-size: 0.72rem; font-weight: 600; padding: 2px 10px;
        border-radius: 999px; margin-right: 8px; color: #112231;
    }
    .monzo-source-card {
        border: 1px solid #EBEBEB; border-radius: 14px; padding: 10px 14px; margin-bottom: 8px;
    }
    .monzo-source-score { color: #6b6b76; font-size: 0.78rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

CATEGORY_COLORS = {
    "help": "#8CE6D1",
    "product": "#FFA9DF",
    "business": "#EBEBEB",
    "security": "#FF4F40",
    "legal": "#EBEBEB",
}

st.title("Monzo AI Knowledge Assistant")
st.caption(
    "Independent portfolio project — not affiliated with, endorsed by, or sponsored by Monzo Bank. "
    "Answers are grounded in Monzo's public website content; retrieval isn't perfect, so double-check anything important."
)


@st.cache_resource
def _load() -> tuple:
    config = load_config(DEFAULT_CONFIG_PATH)
    retrieval_cfg = config.get("retrieval", {})
    index_dir = Path(retrieval_cfg.get("index_dir", "data/processed/retrieval"))
    return load_index(index_dir), config


try:
    index, config = _load()
except FileNotFoundError:
    st.error("Retrieval index not found. Run `python scripts/build_retrieval_index.py` first.")
    st.stop()

assistant_cfg = config.get("assistant", {})
model = assistant_cfg.get("model", "claude-haiku-4-5")
max_tokens = assistant_cfg.get("max_tokens", 1024)
max_queries_per_session = assistant_cfg.get("max_queries_per_session", 10)
# ANALYTICS_DB_PATH overrides config in the deployed Space, pointing the
# db at the mounted persistent Storage Bucket instead of container-local
# disk (which HF wipes on every restart).
db_path = Path(
    os.environ.get("ANALYTICS_DB_PATH")
    or config.get("analytics", {}).get("query_log_path", "data/analytics/query_log.db")
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "query_count" not in st.session_state:
    st.session_state.query_count = 0

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

session_limit_reached = st.session_state.query_count >= max_queries_per_session
if session_limit_reached:
    st.info(
        f"You've reached the {max_queries_per_session}-question limit for this session "
        "(this is a free personal demo, not a production app — the cap keeps API costs bounded). "
        "Open the app in a new tab to start a fresh session."
    )

if prompt := st.chat_input(
    "Ask about Monzo's fees, accounts, security...",
    disabled=session_limit_reached,
):
    st.session_state.query_count += 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        results = search(index, prompt, top_k=5)

        with st.spinner("Thinking..."):
            try:
                generated = generate_answer(prompt, results, model=model, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the user rather than crashing the app
                st.error(f"Couldn't generate an answer: {exc}")
                st.stop()

        st.markdown(generated.answer)

        with st.expander(f"Chunks retrieval considered ({len(results)})"):
            for _, row in results.iterrows():
                pill_color = CATEGORY_COLORS.get(row["category"], "#EBEBEB")
                st.markdown(
                    f"""
                    <div class="monzo-source-card">
                        <span class="monzo-pill" style="background-color: {pill_color};">{row['category']}</span>
                        <a href="{row['url']}" target="_blank"><strong>{row['title']}</strong></a>
                        <div class="monzo-source-score">score {row['score']:.4f}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        log_query(
            db_path,
            query_text=prompt,
            results=results,
            answer_text=generated.answer,
            model=generated.model,
            had_answer=generated.had_answer,
            latency_ms=generated.latency_ms,
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
        )

    st.session_state.messages.append({"role": "assistant", "content": generated.answer})
