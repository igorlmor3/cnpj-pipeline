"""Testes do cliente da Anthropic, com o SDK real e um cliente de rede falso (sem chamar a API)."""

from __future__ import annotations

from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic")

from cnpj_pipeline.agent.llm import RUN_SQL_TOOL, AnthropicClient, LLMError, ToolCall  # noqa: E402


def _message(*blocks, stop_reason="end_turn"):
    return anthropic.types.Message(
        id="msg_1", model="claude-haiku-5-5", role="assistant", type="message", content=list(blocks),
        stop_reason=stop_reason, stop_sequence=None,
        usage=anthropic.types.Usage(input_tokens=1234, output_tokens=56),
    )  # fmt: skip


class FakeSDK:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def sdk_error(cls, status: int | None = None):
    """Cria a excecao do SDK sem passar pelo construtor, que depende da biblioteca HTTP da versao."""
    error = cls.__new__(cls)
    Exception.__init__(error, "corpo do provedor com sk-ant-SEGREDO")
    if status is not None:
        error.status_code = status
    return error


def test_the_anthropic_client_maps_text_tool_calls_and_usage():
    message = _message(
        anthropic.types.TextBlock(type="text", text="Vou consultar."),
        anthropic.types.ToolUseBlock(type="tool_use", id="tu_1", name="run_sql", input={"sql": "SELECT 1"}),
        stop_reason="tool_use",
    )
    sdk = FakeSDK(message)
    client = AnthropicClient(sdk_client=sdk)
    result = client.step("sistema", [{"role": "user", "content": "oi"}], max_tokens=512)

    assert result.text == "Vou consultar."
    assert result.tool_calls == [ToolCall(id="tu_1", sql="SELECT 1")]
    assert (result.input_tokens, result.output_tokens, result.stop_reason) == (1234, 56, "tool_use")
    assert result.content == [
        {"type": "text", "text": "Vou consultar."},
        {"type": "tool_use", "id": "tu_1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
    ]
    request = sdk.requests[0]
    assert request["model"] == "claude-haiku-5-5" and request["max_tokens"] == 512
    assert request["system"] == "sistema" and request["tools"] == [RUN_SQL_TOOL]
    assert request["messages"] == [{"role": "user", "content": "oi"}]


def test_a_tool_call_with_a_non_string_sql_becomes_an_empty_sql():
    message = _message(
        anthropic.types.ToolUseBlock(type="tool_use", id="tu_1", name="run_sql", input={"sql": 42}),
        stop_reason="tool_use",
    )
    result = AnthropicClient(sdk_client=FakeSDK(message)).step("s", [], max_tokens=10)
    assert result.tool_calls == [ToolCall(id="tu_1", sql="")]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (lambda: sdk_error(anthropic.AuthenticationError, 401), "credencial"),
        (lambda: sdk_error(anthropic.RateLimitError, 429), "limitou"),
        (lambda: sdk_error(anthropic.APITimeoutError), "demorou"),
        (lambda: sdk_error(anthropic.APIConnectionError), "conectar"),
        (lambda: sdk_error(anthropic.InternalServerError, 500), "500"),
        (lambda: sdk_error(anthropic.AnthropicError), "falha ao chamar"),
    ],
)
def test_provider_errors_become_safe_llm_errors(error, expected):
    client = AnthropicClient(sdk_client=FakeSDK(error()))
    with pytest.raises(LLMError) as caught:
        client.step("s", [], max_tokens=10)
    assert expected in str(caught.value)
    assert "SEGREDO" not in str(caught.value)


def test_the_anthropic_client_reads_the_key_from_the_environment_and_never_takes_it(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste-nao-real")
    client = AnthropicClient()
    assert "sk-ant-teste-nao-real" not in repr(vars(client).get("model"))
    assert client._client.api_key == "sk-ant-teste-nao-real"
    with pytest.raises(TypeError):
        AnthropicClient(api_key="x")  # type: ignore[call-arg]


def test_the_search_docs_tool_is_offered_only_when_asked_and_its_calls_are_mapped():
    from cnpj_pipeline.agent.llm import SEARCH_DOCS_TOOL, tools_for

    assert tools_for(docs=False) == [RUN_SQL_TOOL] and tools_for(docs=True) == [
        RUN_SQL_TOOL,
        SEARCH_DOCS_TOOL,
    ]
    assert "run_sql" in SEARCH_DOCS_TOOL["description"] and SEARCH_DOCS_TOOL["input_schema"]["required"] == [
        "query"
    ]

    message = _message(
        anthropic.types.ToolUseBlock(type="tool_use", id="a", name="search_docs", input={"query": "DuckDB"}),
        anthropic.types.ToolUseBlock(type="tool_use", id="b", name="search_docs", input={"query": 7}),
        anthropic.types.ToolUseBlock(type="tool_use", id="c", name="run_sql", input={"sql": "SELECT 1"}),
        stop_reason="tool_use",
    )
    sdk = FakeSDK(message)
    result = AnthropicClient(sdk_client=sdk, tools=tools_for(docs=True)).step("s", [], max_tokens=10)

    assert sdk.requests[0]["tools"] == [RUN_SQL_TOOL, SEARCH_DOCS_TOOL]
    assert result.tool_calls == [
        ToolCall(id="a", sql="", name="search_docs", query="DuckDB"),
        ToolCall(id="b", sql="", name="search_docs", query=""),
        ToolCall(id="c", sql="SELECT 1"),
    ]
