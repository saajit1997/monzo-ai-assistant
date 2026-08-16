"""Phase 7 answer-generation tests. The Anthropic client is monkeypatched
with a fake -- these never call the real API (no cost, no key needed, no
network).
"""

from __future__ import annotations

import pandas as pd

from monzo_ai.assistant import generate
from monzo_ai.assistant.generate import NO_INFO_MARKER, generate_answer


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn", model: str = "claude-haiku-4-5"):
        self.content = [_FakeTextBlock(text)] if stop_reason != "refusal" else []
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _FakeUsage(120, 40)


class _FakeMessages:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.messages = _FakeMessages(response)


def _chunks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"url": "https://example.com/help/fees", "category": "help", "title": "Fees", "text": "Cash withdrawals abroad: £400 fee-free.", "score": 0.05},
            {"url": "https://example.com/help/cards", "category": "help", "title": "Cards", "text": "Freeze your card in the app.", "score": 0.03},
        ]
    )


def test_generate_answer_returns_answer_and_metadata(monkeypatch):
    fake = _FakeClient(_FakeResponse("You can withdraw up to £400 fee-free.\nSources: https://example.com/help/fees"))
    monkeypatch.setattr(generate, "_client", lambda: fake)

    result = generate_answer("what's the fee-free limit", _chunks())

    assert "£400" in result.answer
    assert result.model == "claude-haiku-4-5"
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 120
    assert result.output_tokens == 40
    assert result.had_answer is True
    assert result.latency_ms >= 0


def test_context_includes_every_chunk_url_and_text(monkeypatch):
    fake = _FakeClient(_FakeResponse("answer"))
    monkeypatch.setattr(generate, "_client", lambda: fake)

    generate_answer("a question", _chunks())

    sent_message = fake.messages.last_kwargs["messages"][0]["content"]
    assert "https://example.com/help/fees" in sent_message
    assert "https://example.com/help/cards" in sent_message
    assert "Cash withdrawals abroad: £400 fee-free." in sent_message
    assert "a question" in sent_message


def test_had_answer_false_when_model_uses_no_info_marker(monkeypatch):
    fake = _FakeClient(_FakeResponse(NO_INFO_MARKER))
    monkeypatch.setattr(generate, "_client", lambda: fake)

    result = generate_answer("something unrelated", _chunks())

    assert result.had_answer is False
    assert result.answer == NO_INFO_MARKER


def test_refusal_stop_reason_handled_without_reading_empty_content(monkeypatch):
    fake = _FakeClient(_FakeResponse("", stop_reason="refusal"))
    monkeypatch.setattr(generate, "_client", lambda: fake)

    result = generate_answer("some borderline question", _chunks())

    assert result.had_answer is False
    assert result.stop_reason == "refusal"
    assert "not able to answer" in result.answer.lower()


def test_model_and_max_tokens_are_passed_through(monkeypatch):
    fake = _FakeClient(_FakeResponse("answer"))
    monkeypatch.setattr(generate, "_client", lambda: fake)

    generate_answer("q", _chunks(), model="claude-haiku-4-5", max_tokens=256)

    assert fake.messages.last_kwargs["model"] == "claude-haiku-4-5"
    assert fake.messages.last_kwargs["max_tokens"] == 256
