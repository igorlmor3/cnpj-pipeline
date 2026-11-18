"""Cliente do modelo de linguagem, atras de uma interface pequena.

O agente so conhece LLMClient. A implementacao da Anthropic importa o SDK so quando e criada
(o SDK fica no extra "agent"), le a chave da variavel de ambiente ANTHROPIC_API_KEY e nunca a
guarda, imprime ou devolve. Os erros do provedor viram LLMError com mensagem segura, sem o corpo
da resposta.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "claude-haiku-5-5"
TOOL_NAME = "run_sql"
SEARCH_DOCS_NAME = "search_docs"

RUN_SQL_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Executa uma consulta SELECT sobre as tabelas agregadas do cadastro de CNPJ e devolve as "
        "colunas e as linhas. Se o SQL for recusado ou falhar, devolve a mensagem de erro."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "Uma unica instrução SELECT (Trino)."}},
        "required": ["sql"],
    },
}

SEARCH_DOCS_TOOL: dict[str, Any] = {
    "name": SEARCH_DOCS_NAME,
    "description": (
        "Busca trechos da documentação pública do projeto (arquitetura, decisões, regras de qualidade "
        "de dados, método e avaliação do agente) e devolve os mais relevantes, cada um com a fonte. "
        "Use para perguntas sobre como o projeto foi feito, por que uma decisão foi tomada ou o que "
        "uma regra significa. Não use para números do cadastro de CNPJ: esses vêm de run_sql."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "O que procurar, em poucas palavras-chave."}
        },
        "required": ["query"],
    },
}


def tools_for(*, docs: bool) -> list[dict[str, Any]]:
    """As ferramentas oferecidas ao modelo: run_sql e, se houver busca na documentacao, search_docs."""
    return [RUN_SQL_TOOL, SEARCH_DOCS_TOOL] if docs else [RUN_SQL_TOOL]


class LLMError(RuntimeError):
    """Falha ao falar com o modelo. A mensagem e segura para exibir."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    sql: str  # o SQL pedido, quando a ferramenta e run_sql
    name: str = TOOL_NAME
    query: str = ""  # o texto da busca, quando a ferramenta e search_docs


@dataclass(frozen=True)
class LLMTurn:
    """Uma resposta do modelo: texto, pedidos de ferramenta e o uso de tokens."""

    text: str
    tool_calls: list[ToolCall]
    content: list[dict[str, Any]]  # blocos da resposta, para devolver ao modelo na proxima rodada
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    def step(self, system: str, messages: list[dict[str, Any]], *, max_tokens: int) -> LLMTurn: ...


class AnthropicClient:
    """Chama a API de mensagens da Anthropic com a ferramenta run_sql."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        sdk_client: Any = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        import anthropic  # so aqui: quem usa outro cliente (ou os testes) nao precisa do SDK

        self._errors = anthropic
        self.model = model
        self._tools = list(tools) if tools is not None else [RUN_SQL_TOOL]
        # A chave vem de ANTHROPIC_API_KEY, lida pelo proprio SDK.
        self._client = sdk_client or anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)

    def step(self, system: str, messages: list[dict[str, Any]], *, max_tokens: int) -> LLMTurn:
        sdk = self._errors
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                tools=self._tools,
                messages=messages,
            )
        except sdk.AuthenticationError as exc:
            raise LLMError("a credencial do provedor do modelo foi recusada") from exc
        except sdk.RateLimitError as exc:
            raise LLMError("o provedor do modelo limitou as requisicoes; tente de novo em instantes") from exc
        except sdk.APITimeoutError as exc:
            raise LLMError("o provedor do modelo demorou demais para responder") from exc
        except sdk.APIConnectionError as exc:
            raise LLMError("nao foi possivel conectar ao provedor do modelo") from exc
        except sdk.APIStatusError as exc:
            raise LLMError(f"o provedor do modelo devolveu um erro (codigo {exc.status_code})") from exc
        except sdk.AnthropicError as exc:
            raise LLMError("falha ao chamar o modelo") from exc
        return _to_turn(response)


def _to_turn(response: Any) -> LLMTurn:
    content: list[dict[str, Any]] = []
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
            content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            raw = block.input if isinstance(block.input, dict) else {}
            if block.name == SEARCH_DOCS_NAME:
                query = raw.get("query")
                calls.append(
                    ToolCall(
                        id=block.id,
                        sql="",
                        name=SEARCH_DOCS_NAME,
                        query=query if isinstance(query, str) else "",
                    )
                )
            else:
                sql = raw.get("sql")
                calls.append(ToolCall(id=block.id, sql=sql if isinstance(sql, str) else ""))
            content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": raw})
    return LLMTurn(
        text="\n".join(t for t in texts if t).strip(),
        tool_calls=calls,
        content=content,
        input_tokens=int(response.usage.input_tokens),
        output_tokens=int(response.usage.output_tokens),
        stop_reason=response.stop_reason,
    )
