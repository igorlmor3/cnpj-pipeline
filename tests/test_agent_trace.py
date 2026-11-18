"""Testes do trajeto da resposta ("como chegou la")."""

from __future__ import annotations

import json

from helpers import MONTH
from test_agent_ask import GOOD_SQL, FakeLLM, doc_index, docs_turn, turn
from test_api import Rig, body_of, executor, gold_root, system  # noqa: F401  (fixtures)

from cnpj_pipeline.agent.ask import Answer, DocSearch, QueryRecord
from cnpj_pipeline.agent.trace import KEYS, MAX_UNSUPPORTED_SHOWN, build_trace


def ok_query(rows=None, **kw):
    return QueryRecord(
        sql="SELECT uf, n FROM t", ok=True, columns=["uf", "n"], rows=rows or [["SP", 10]], **kw
    )


def test_the_trace_lists_queries_searches_usage_and_the_fidelity_check():
    answer = Answer(
        "answered",
        "SP tem 10 empresas.",
        queries=[
            QueryRecord(sql="SELECT * FROM x", ok=False, error="tabela fora da lista"),
            ok_query(bytes_scanned=2048, may_be_truncated=True),
        ],
        docs=[DocSearch(query="DuckDB", sources=["README.md, seção Motor"], shown="[1] fonte: README.md")],
        llm_calls=3,
        input_tokens=4000,
        output_tokens=300,
        cost_usd=0.00123456789,
    )
    trace = build_trace("Quantas empresas em SP?", answer)

    failed, good = trace["queries"]
    assert failed == {
        "sql": "SELECT * FROM x", "ok": False, "error": "tabela fora da lista",
        "rows": None, "cut": None, "bytes_scanned": None,
    }  # fmt: skip
    assert good["ok"] and good["rows"] == 1 and good["cut"] is True and good["bytes_scanned"] == 2048
    assert good["error"] is None
    assert trace["doc_searches"] == [{"query": "DuckDB", "ok": True, "sources": ["README.md, seção Motor"]}]
    assert (trace["llm_calls"], trace["input_tokens"], trace["output_tokens"]) == (3, 4000, 300)
    assert trace["cost_usd"] == 0.001235
    assert trace["fidelity"] == {"numbers_checked": 1, "faithful": True, "unsupported": []}


def test_an_invented_number_shows_up_as_unsupported_and_the_list_is_capped():
    text = "Foram " + ", ".join(str(9000 + i) for i in range(20)) + " empresas."
    trace = build_trace("quantas?", Answer("answered", text, queries=[ok_query()]))

    assert trace["fidelity"]["faithful"] is False
    assert trace["fidelity"]["numbers_checked"] == 20
    assert len(trace["fidelity"]["unsupported"]) == MAX_UNSUPPORTED_SHOWN


def test_only_an_answered_status_has_a_fidelity_check():
    for status in ("out_of_scope", "failed"):
        assert "fidelity" not in build_trace("x?", Answer(status, "texto"))


def test_long_error_and_search_texts_are_cut():
    answer = Answer(
        "failed", "",
        queries=[QueryRecord(sql="SELECT 1", ok=False, error="e" * 1000)],
        docs=[DocSearch(query="q" * 1000, ok=False)],
    )  # fmt: skip
    trace = build_trace("x?", answer)
    assert len(trace["queries"][0]["error"]) == 300 and len(trace["doc_searches"][0]["query"]) == 300


def test_the_trace_never_carries_rows_the_question_or_the_documentation_text():
    answer = Answer(
        "answered", "SP: 10.", queries=[ok_query(rows=[["SEGREDO", 10]])],
        docs=[DocSearch(query="q", sources=["a.md"], shown="TEXTO DE DOCUMENTO")],
    )  # fmt: skip
    raw = json.dumps(build_trace("PERGUNTA DO VISITANTE", answer), ensure_ascii=False)
    assert "SEGREDO" not in raw and "PERGUNTA DO VISITANTE" not in raw and "TEXTO DE DOCUMENTO" not in raw


def test_both_languages_have_the_same_fields_and_the_portuguese_one_is_used_for_the_examples():
    assert set(KEYS["en"]) == set(KEYS["pt"])
    answer = Answer("answered", "SP tem 10.", queries=[ok_query()])
    pt = build_trace("x?", answer, lang="pt")
    assert {"consultas", "buscas_docs", "chamadas_modelo", "custo_usd", "fidelidade"} <= set(pt)
    assert pt["consultas"][0]["linhas"] == 1 and pt["fidelidade"]["fiel"] is True


def test_the_api_answer_carries_the_trace_but_a_refusal_for_a_bad_question_does_not(executor, system):  # noqa: F811
    rig = Rig(executor, system)
    body = body_of(rig.ask())
    assert body["status"] == "answered" and body["trace"]["queries"][0]["ok"] is True
    # a resposta do modelo falso nao tem numero: nada a verificar, e `faithful` fica nulo
    assert body["trace"]["fidelity"] == {"numbers_checked": 0, "faithful": None, "unsupported": []}

    refused = body_of(Rig(executor, system).ask(question=" "))
    assert "trace" not in refused


def test_the_api_trace_of_a_documentation_answer_lists_the_search_and_no_queries(executor, system):  # noqa: F811
    rig = Rig(
        executor, system, script=[docs_turn("DuckDB CSV memória"), turn("Porque lê CSV.")],
        retriever=doc_index(),
    )  # fmt: skip
    trace = body_of(rig.ask("Por que DuckDB?"))["trace"]
    assert trace["queries"] == [] and trace["doc_searches"][0]["sources"] == [
        "docs/architecture.md, seção Motor"
    ]


def test_an_out_of_scope_answer_keeps_a_trace_without_fidelity(executor, system):  # noqa: F811
    body = body_of(Rig(executor, system, script=[turn("Não sei.")]).ask("Qual o CNPJ da empresa X?"))
    assert body["status"] == "out_of_scope" and "fidelity" not in body["trace"]
    assert body["trace"]["llm_calls"] == 1


def test_recorded_examples_get_the_trace_in_portuguese(gold_root):  # noqa: F811
    from cnpj_pipeline.agent import record_examples as rec

    llm = FakeLLM(turn("", GOOD_SQL), turn("SP tem a maior contagem."))
    example = rec.Example("x", "Quantas empresas ativas por UF?")
    from cnpj_pipeline.agent.ask import ask, build_system_prompt
    from cnpj_pipeline.agent.context import TABLES
    from cnpj_pipeline.agent.executor import DuckDBExecutor
    from cnpj_pipeline.cloud.athena import gold_columns

    ex = DuckDBExecutor(gold_root)
    try:
        system_prompt = build_system_prompt(gold_columns(gold_root, MONTH, list(TABLES)), month=MONTH)
        answer = ask(example.question, month=MONTH, system=system_prompt, llm=llm, executor=ex)
    finally:
        ex.close()
    entry = rec.example_entry(example, answer)

    assert entry["trajeto"]["consultas"][0]["ok"] is True and entry["trajeto"]["chamadas_modelo"] == 2
    assert entry["linhas"]  # o resto da entrada continua igual
