"""O trajeto de uma resposta: o que o agente fez para chegar nela, sem expor mais do que ja e publico.

Alimenta a secao "como chegou la" do site, a partir do que o programa observou (as consultas
executadas, as buscas na documentacao, o uso do modelo e a verificacao de fidelidade) e nao de uma
descricao que o modelo escreve de si mesmo: o modelo nao precisa produzir nada alem do texto da
resposta, e nada aqui pode divergir do que de fato aconteceu.

Nao entram no trajeto as linhas dos resultados (a resposta ja traz as do ultimo resultado), a pergunta
nem o prompt.
"""

from __future__ import annotations

from typing import Any

from cnpj_pipeline.agent.ask import Answer
from cnpj_pipeline.agent.faithfulness import check_answer

MAX_UNSUPPORTED_SHOWN = 8
MAX_STEP_TEXT_CHARS = 300  # mensagem de erro ou consulta de busca mostrada no trajeto

# Nomes dos campos nos dois formatos: a API fala em ingles, os exemplos gravados do site em portugues.
KEYS = {
    "en": {
        "queries": "queries", "sql": "sql", "ok": "ok", "error": "error", "rows": "rows",
        "cut": "cut", "bytes": "bytes_scanned", "docs": "doc_searches", "query": "query",
        "sources": "sources", "llm": "llm_calls", "tin": "input_tokens", "tout": "output_tokens",
        "cost": "cost_usd", "fidelity": "fidelity", "checked": "numbers_checked",
        "unsupported": "unsupported", "faithful": "faithful",
    },
    "pt": {
        "queries": "consultas", "sql": "sql", "ok": "ok", "error": "erro", "rows": "linhas",
        "cut": "cortado", "bytes": "bytes_lidos", "docs": "buscas_docs", "query": "consulta",
        "sources": "fontes", "llm": "chamadas_modelo", "tin": "tokens_entrada", "tout": "tokens_saida",
        "cost": "custo_usd", "fidelity": "fidelidade", "checked": "numeros_verificados",
        "unsupported": "sem_apoio", "faithful": "fiel",
    },
}  # fmt: skip


def build_trace(question: str, answer: Answer, *, lang: str = "en") -> dict[str, Any]:
    """Resumo estruturado do que o agente fez. `lang` escolhe os nomes dos campos ("en" ou "pt")."""
    k = KEYS[lang]
    trace: dict[str, Any] = {
        k["queries"]: [
            {
                k["sql"]: q.sql,
                k["ok"]: q.ok,
                k["error"]: (q.error or "")[:MAX_STEP_TEXT_CHARS] or None,
                k["rows"]: len(q.rows) if q.ok else None,
                k["cut"]: q.may_be_truncated if q.ok else None,
                k["bytes"]: q.bytes_scanned if q.ok else None,
            }
            for q in answer.queries
        ],
        k["docs"]: [
            {k["query"]: d.query[:MAX_STEP_TEXT_CHARS], k["ok"]: d.ok, k["sources"]: list(d.sources)}
            for d in answer.docs
        ],
        k["llm"]: answer.llm_calls,
        k["tin"]: answer.input_tokens,
        k["tout"]: answer.output_tokens,
        k["cost"]: round(answer.cost_usd, 6),
    }
    if answer.status == "answered":  # `faithful` e nulo quando o texto nao tem nenhum numero
        check = check_answer(question, answer)
        trace[k["fidelity"]] = {
            k["checked"]: check.checked,
            k["faithful"]: check.faithful,
            k["unsupported"]: check.unsupported[:MAX_UNSUPPORTED_SHOWN],
        }
    return trace
