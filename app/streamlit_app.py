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
db_path = Path(config.get("analytics", {}).get("query_log_path", "data/analytics/query_log.db"))

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask about Monzo's fees, accounts, security..."):
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
                st.markdown(f"- [{row['title']}]({row['url']}) — score {row['score']:.4f}")

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
