"""Phase 7: generate a grounded answer from Phase 6's retrieved chunks.

Uses the Claude API directly (a single Messages call, no tool use, no
agentic loop -- this is a Q&A task, not a workflow). Grounding is enforced
entirely through the system prompt: answer only from the provided context,
cite sources, and use a fixed marker phrase when the context doesn't
contain the answer, so callers can detect "no answer" without parsing
free-form text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import pandas as pd

from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 1024

NO_INFO_MARKER = "I don't have enough information from Monzo's public site to answer that."

SYSTEM_PROMPT = f"""You are a customer-support assistant for an independent portfolio project that answers questions about Monzo Bank's public products, fees, and policies. You are NOT an official Monzo product, employee, or representative, and must never imply otherwise.

Answer ONLY using the CONTEXT below, which is made up of numbered excerpts retrieved from monzo.com. Do not use any outside knowledge about Monzo, banking, or financial products in general -- if it isn't in the CONTEXT, you don't know it.

Rules:
- If the CONTEXT does not contain enough information to answer the question, respond with exactly this sentence and nothing else: "{NO_INFO_MARKER}"
- Never invent or estimate a fee, rate, limit, or policy detail that isn't stated in the CONTEXT.
- End every answer that does contain information with a "Sources:" line listing the URL(s) of the excerpt(s) you actually used.
- Be concise and direct -- a few sentences, not an essay.
"""


@dataclass
class GeneratedAnswer:
    answer: str
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    had_answer: bool


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _build_context(chunks: pd.DataFrame) -> str:
    blocks = [f"[{i}] Source: {row.url}\n{row.text}" for i, row in enumerate(chunks.itertuples(), start=1)]
    return "\n\n".join(blocks)


def generate_answer(
    query: str,
    chunks: pd.DataFrame,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> GeneratedAnswer:
    """Calls Claude to answer query grounded in chunks (Phase 6's retrieval
    output). Raises the underlying anthropic exception on failure -- callers
    decide how to surface that (e.g. the Streamlit app shows it inline).
    """
    context = _build_context(chunks)
    user_message = f"CONTEXT:\n{context}\n\nQUESTION: {query}"

    client = _client()
    start = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.AuthenticationError:
        logger.error("Claude API authentication failed -- check ANTHROPIC_API_KEY")
        raise
    except anthropic.APIStatusError as exc:
        logger.error("Claude API error (%s): %s", exc.status_code, exc.message)
        raise
    except anthropic.APIConnectionError as exc:
        logger.error("Could not reach the Claude API: %s", exc)
        raise
    latency_ms = int((time.monotonic() - start) * 1000)

    if response.stop_reason == "refusal":
        logger.warning("Claude declined to answer: %r", query)
        answer_text = "I'm not able to answer that question."
        had_answer = False
    else:
        answer_text = "".join(block.text for block in response.content if block.type == "text")
        had_answer = not answer_text.strip().startswith(NO_INFO_MARKER)

    return GeneratedAnswer(
        answer=answer_text,
        model=response.model,
        stop_reason=response.stop_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        had_answer=had_answer,
    )
