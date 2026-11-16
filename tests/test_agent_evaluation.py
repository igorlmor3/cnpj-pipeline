"""Testes da avaliacao: comparacao de resultados, julgamento dos casos, metricas, relatorio e CLI.

O ponto central e provar que a avaliacao reprova respostas erradas, nao so que aprova as certas.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest
from helpers import MONTH
from test_gold import build_silver, run_gold

from cnpj_pipeline.agent import evaluation
from cnpj_pipeline.agent.ask import Answer, QueryRecord, build_system_prompt
from cnpj_pipeline.agent.budget import Budget, Pricing
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.evaluation import (
    REASON_DIFFERENT,
    REASON_FORMAT,
    CaseResult,
    EvaluationError,
    ReferenceLLM,
    evaluate,
    expected_rows,
    judge,
    main,
    render_report,
    results_json,
    rows_contain,
    rows_match,
    stability,
    summarize,
)
from cnpj_pipeline.agent.evaluation_cases import (
    ALL_CASES,
    CASES,
    HOLDOUT_CASES,
    ROBUSTEZ_CASES,
    SEM_POLITICAS,
    SUITES,
    EvalCase,
    by_id,
)
from cnpj_pipeline.agent.executor import DuckDBExecutor
from cnpj_pipeline.agent.llm import LLMTurn, ToolCall
from cnpj_pipeline.cloud.athena import gold_columns

SIMPLES = "gold_mart_simples_mei_uf"


# --------------------------------------------------------------------------- #
# Apoio
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("evalgold")
    build_silver(base)
    assert run_gold(base) == 0
    return base / "gold"


@pytest.fixture()
def executor(gold_root):
    ex = DuckDBExecutor(gold_root)
    yield ex
    ex.close()


@pytest.fixture(scope="module")
def system(gold_root):
    return build_system_prompt(gold_columns(gold_root, MONTH, list(TABLES)), month=MONTH)


class QuestionLLM:
    """Modelo falso que decide o SQL (ou nenhum) a partir da pergunta; depois responde com um texto."""

    def __init__(self, sql_for, final: str = "Pronto.") -> None:
        self.sql_for = sql_for  # funcao: EvalCase -> str | None
        self.final = final
        self._by_question = {c.question: c for c in ALL_CASES}

    def step(self, system: str, messages: list[dict[str, Any]], *, max_tokens: int) -> LLMTurn:
        case = self._by_question[messages[0]["content"]]
        sql = self.sql_for(case)
        if len(messages) == 1 and sql:
            content = [{"type": "tool_use", "id": "q", "name": "run_sql", "input": {"sql": sql}}]
            return LLMTurn("", [ToolCall("q", sql)], content, 10, 5)
        return LLMTurn(self.final, [], [{"type": "text", "text": self.final}], 10, 5)


def run(cases, llm, executor, system, budget=None):
    return {
        r.id: r
        for r in evaluate(cases, month=MONTH, system=system, llm=llm, executor=executor, budget=budget)
    }


def make_answer(status="answered", queries=(), text="ok") -> Answer:
    return Answer(status=status, text=text, queries=list(queries))


def ok_query(rows, sql="SELECT 1") -> QueryRecord:
    return QueryRecord(sql=sql, ok=True, columns=["c"], rows=rows)


def bad_query(sql="SELECT 1") -> QueryRecord:
    return QueryRecord(sql=sql, ok=False, error="recusado")


# --------------------------------------------------------------------------- #
# rows_match
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("expected", "got", "ordered", "result"),
    [
        ([["SP", 2], ["MG", 1]], [["SP", 2], ["MG", 1]], True, True),
        ([["SP", 2], ["MG", 1]], [["MG", 1], ["SP", 2]], False, True),
        ([["SP", 2], ["MG", 1]], [["MG", 1], ["SP", 2]], True, False),  # a ordem conta nos rankings
        ([["SP", 2]], [["SP", 2, "extra"]], False, True),  # coluna a mais
        ([["SP", 2]], [["rotulo", 2, "SP"]], False, True),  # colunas a mais e em outra ordem
        ([["SP", 2]], [[2, "SP"]], False, True),  # colunas trocadas
        ([["12.00"]], [[12]], False, True),  # decimal em texto contra inteiro
        ([[73.4567]], [[73.5]], False, True),  # numero nao inteiro: 1 casa
        ([[73.4567]], [[73.6]], False, False),
        ([["sao paulo"]], [["SAO PAULO"]], False, True),  # sem diferenca de caixa
        ([["SAO PAULO"]], [["SÃO PAULO"]], False, False),  # acento nao e ignorado
        ([[1]], [[2]], False, False),
        ([[1], [2]], [[1]], False, False),  # faltou linha
        ([[1]], [[1], [1]], False, False),  # linha a mais
        ([[1], [1], [2]], [[1], [2], [2]], False, False),  # multiconjunto, nao conjunto
        ([["SP", 2]], [["SP"]], False, False),  # menos colunas
        ([], [], False, True),
        ([[1]], [], False, False),
        ([], [[1]], False, False),
        ([[None]], [[None]], False, True),
        ([[None]], [[0]], False, False),
        ([[float("nan")]], [[float("nan")]], False, True),  # nao quebra
    ],
)
def test_rows_match(expected, got, ordered, result):
    assert rows_match(expected, got, ordered=ordered) is result


@pytest.mark.parametrize(
    ("expected", "got", "result"),
    [
        ([[5]], [[5], [7]], True),  # linha a mais
        ([[5]], [["pessoa fisica", 5], ["juridica", 7]], True),  # a quebra por grupo traz o valor
        ([["SP", 2]], [["MG", 1], ["SP", 2]], True),
        ([[1], [1]], [[1], [2]], False),  # multiconjunto: faltou um 1
        ([[5]], [[6], [7]], False),
        ([["SP", 2]], [["SP", 3], ["MG", 2]], False),  # os dois valores existem, mas em linhas diferentes
        ([["SP", 2]], [["SP"]], False),
        ([[1], [2]], [[1]], False),  # o esperado tem mais linhas que o devolvido
        ([], [[1]], False),
        ([[1]], [], False),
    ],
)
def test_rows_contain(expected, got, result):
    assert rows_contain(expected, got) is result


# --------------------------------------------------------------------------- #
# Integridade do conjunto de casos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cases", [CASES, HOLDOUT_CASES, ROBUSTEZ_CASES], ids=["dev", "holdout", "robustez"])
def test_each_case_set_is_well_formed(cases):
    assert len({c.id for c in cases}) == len(cases)
    assert {c.kind for c in cases} == {"data", "caveat", "refusal"}
    for c in cases:
        if c.kind == "refusal":
            assert c.reference_sql is None and not c.must_contain
        else:
            assert c.reference_sql, c.id
        if c.kind == "caveat":
            assert "is_organizacao_politica" in c.must_contain
    assert any(c.kind == "data" and c.ordered for c in cases)


def test_the_suites_do_not_share_ids_questions_or_references():
    assert len({c.id for c in ALL_CASES}) == len(ALL_CASES)
    # o modo de referencia indexa pela pergunta
    assert len({c.question for c in ALL_CASES}) == len(ALL_CASES)
    dev_sql = {c.reference_sql for c in CASES if c.reference_sql}
    assert not [c.id for c in HOLDOUT_CASES if c.reference_sql in dev_sql]
    assert not [c.id for c in ROBUSTEZ_CASES if c.reference_sql in dev_sql | holdout_sql()]
    assert all(c.id.startswith("ho_") for c in HOLDOUT_CASES)
    assert all(c.id.startswith("rb_") for c in ROBUSTEZ_CASES)
    assert not any(c.id.startswith(("ho_", "rb_")) for c in CASES)
    assert SUITES == {"dev": CASES, "holdout": HOLDOUT_CASES, "robustez": ROBUSTEZ_CASES, "all": ALL_CASES}
    assert len(ALL_CASES) == len(CASES) + len(HOLDOUT_CASES) + len(ROBUSTEZ_CASES)


def holdout_sql() -> set[str]:
    return {c.reference_sql for c in HOLDOUT_CASES if c.reference_sql}


def test_the_holdout_covers_every_category_of_the_dev_set():
    assert {c.category for c in HOLDOUT_CASES} == {c.category for c in CASES}
    assert len(HOLDOUT_CASES) >= 20


def test_the_robustness_set_covers_what_the_other_two_do_not():
    assert len(ROBUSTEZ_CASES) >= 30
    assert {c.kind for c in ROBUSTEZ_CASES} == {"data", "caveat", "refusal"}
    # categorias proprias: frases informais, filtros por nome, contas derivadas, comparacoes e rankings
    # que nao sao "o maior"
    assert {"informal", "filtro_por_nome", "derivada", "comparacao", "ranking_variante"} <= {
        c.category for c in ROBUSTEZ_CASES
    }
    # a ressalva das organizacoes politicas nao vem escrita em pelo menos metade das perguntas de dados
    data = [c for c in ROBUSTEZ_CASES if c.kind == "data"]
    assert sum(SEM_POLITICAS not in c.question for c in data) >= len(data) // 2


def test_every_reference_runs_through_the_guard_on_the_real_gold(executor):
    expected = expected_rows(ALL_CASES, executor=executor, month=MONTH)
    assert set(expected) == {c.id for c in ALL_CASES if c.reference_sql}


def test_the_references_are_deterministic_and_run_through_the_guard(executor, system):
    ran: list[str] = []

    class Spy:
        dialect = executor.dialect

        def run(self, sql, *, max_rows):
            ran.append(sql)
            return executor.run(sql, max_rows=max_rows)

    expected_rows(ALL_CASES, executor=Spy(), month=MONTH)
    assert ran and all("reference_month" in sql for sql in ran)  # o mes foi fixado pela guarda

    # empates sem desempate davam resultados diferentes de uma execucao para outra
    for _ in range(10):
        results = evaluate(ALL_CASES, month=MONTH, system=system, llm=ReferenceLLM(), executor=executor)
        assert [r.id for r in results if not r.passed] == []


def test_by_id_selects_and_rejects_unknown_ids():
    assert [c.id for c in by_id(["ativas_sp", "top5_ufs_ativas"])] == ["ativas_sp", "top5_ufs_ativas"]
    with pytest.raises(KeyError):
        by_id(["nao_existe"])


def test_a_broken_reference_fails_before_any_model_call(executor, system):
    broken = EvalCase("x", "t", "data", "pergunta quebrada?", "SELECT * FROM silver_estabelecimentos")
    llm = QuestionLLM(lambda c: None)
    llm._by_question["pergunta quebrada?"] = broken
    with pytest.raises(EvaluationError, match="'x'"):
        evaluate((broken,), month=MONTH, system=system, llm=llm, executor=executor)


# --------------------------------------------------------------------------- #
# judge (sem banco)
# --------------------------------------------------------------------------- #

DATA = EvalCase("d", "t", "data", "q?", "SELECT 1")
ORDERED = EvalCase("o", "t", "data", "q2?", "SELECT 1", ordered=True)
CAVEAT = EvalCase(
    "c",
    "t",
    "caveat",
    "q3?",
    "SELECT 1",
    must_contain=("is_organizacao_politica",),
    answer_must_mention=("elei",),
)
REFUSAL = EvalCase("r", "t", "refusal", "q4?")


def test_judge_refusal_cases():
    assert judge(REFUSAL, make_answer("out_of_scope"), None) == ""
    assert "deveria recusar" in judge(REFUSAL, make_answer("answered", [ok_query([[1]])]), None)
    assert "deveria recusar" in judge(REFUSAL, make_answer("failed"), None)


def test_judge_data_cases():
    expected = [[5]]
    assert judge(DATA, make_answer(queries=[ok_query([[5]])]), expected) == ""
    assert "diferente" in judge(DATA, make_answer(queries=[ok_query([[6]])]), expected)
    assert "estado failed" in judge(DATA, make_answer("failed", [ok_query([[5]])]), expected)
    assert "estado out_of_scope" in judge(DATA, make_answer("out_of_scope"), expected)
    assert "nenhuma consulta" in judge(DATA, make_answer(queries=[bad_query()]), expected)


def test_judge_tells_a_format_error_from_a_wrong_value():
    expected = [[5]]
    wrong = judge(DATA, make_answer(queries=[ok_query([[6]])]), expected)
    breakdown = judge(DATA, make_answer(queries=[ok_query([["a", 5], ["b", 6]])]), expected)
    assert wrong == REASON_DIFFERENT and breakdown == REASON_FORMAT
    assert breakdown.startswith(REASON_DIFFERENT)  # continua sendo erro


def test_judge_does_not_call_a_wrong_order_a_format_error():
    expected = [["SP", 2], ["MG", 1]]
    swapped = ok_query([["MG", 1], ["SP", 2]])
    assert judge(ORDERED, make_answer(queries=[swapped]), expected) == REASON_DIFFERENT


def test_judge_uses_the_last_successful_query():
    expected = [[5]]
    right_last = make_answer(queries=[ok_query([[9]]), bad_query(), ok_query([[5]])])
    wrong_last = make_answer(queries=[ok_query([[5]]), ok_query([[9]])])
    assert judge(DATA, right_last, expected) == ""
    assert "diferente" in judge(DATA, wrong_last, expected)


def test_judge_ordered_cases_check_the_order():
    expected = [["SP", 2], ["MG", 1]]
    assert judge(ORDERED, make_answer(queries=[ok_query([["SP", 2], ["MG", 1]])]), expected) == ""
    assert "diferente" in judge(ORDERED, make_answer(queries=[ok_query([["MG", 1], ["SP", 2]])]), expected)


def test_judge_caveat_cases():
    flagged = ok_query([[1]], "SELECT x FROM t WHERE NOT IS_ORGANIZACAO_POLITICA")
    plain = ok_query([[1]], "SELECT x FROM t")
    assert (
        judge(CAVEAT, make_answer(queries=[flagged], text="Por causa das eleições municipais."), None) == ""
    )
    assert "não usou" in judge(CAVEAT, make_answer(queries=[plain], text="eleições"), None)
    assert "não menciona" in judge(CAVEAT, make_answer(queries=[flagged], text="Subiu."), None)
    # a flag so numa consulta recusada nao conta
    only_in_failed = make_answer(
        queries=[bad_query("... is_organizacao_politica ..."), plain], text="eleições"
    )
    assert "não usou" in judge(CAVEAT, only_in_failed, None)


# --------------------------------------------------------------------------- #
# Avaliacao de ponta a ponta com modelos falsos
# --------------------------------------------------------------------------- #


def test_the_reference_mode_passes_every_case(executor, system):
    results = evaluate(ALL_CASES, month=MONTH, system=system, llm=ReferenceLLM(), executor=executor)
    assert [r.reason for r in results if not r.passed] == []
    assert len(results) == len(ALL_CASES)


def test_a_model_that_queries_the_wrong_thing_fails_the_data_cases(executor, system):
    wrong = f"SELECT SUM(empresas_ativas) FROM {SIMPLES} WHERE uf = 'ZZ'"
    results = run(CASES, QuestionLLM(lambda c: wrong), executor, system)

    for case_id in ("ativas_sp", "top5_ufs_ativas", "ativas_por_porte", "socios_pessoa_fisica"):
        assert not results[case_id].passed, case_id
    assert all(
        not r.passed and "deveria recusar" in r.reason for r in results.values() if r.kind == "refusal"
    )
    assert all(not r.passed and "não usou" in r.reason for r in results.values() if r.kind == "caveat")


def test_a_model_that_refuses_everything_passes_only_the_refusals(executor, system):
    results = run(CASES, QuestionLLM(lambda c: None), executor, system)

    assert all(r.passed for r in results.values() if r.kind == "refusal")
    assert all(not r.passed and "out_of_scope" in r.reason for r in results.values() if r.kind != "refusal")


def test_a_model_that_tries_a_forbidden_table_and_gives_up_still_counts_as_refusing(executor, system):
    case = by_id(["recusa_injecao_silver"])
    llm = QuestionLLM(lambda c: "SELECT * FROM silver_estabelecimentos LIMIT 10")
    result = run(case, llm, executor, system)["recusa_injecao_silver"]

    assert result.passed and result.status == "out_of_scope"
    assert result.queries == 1 and result.failed_queries == 1 and result.first_query_ok is False


def test_extra_labels_and_reordered_columns_are_accepted(executor, system):
    def sql_for(case):
        return {
            "ativas_por_porte": (
                "SELECT porte_descricao, SUM(empresas_ativas) AS n FROM gold_mart_perfil_porte_natureza "
                "WHERE NOT is_organizacao_politica GROUP BY porte_descricao"
            ),
            "top5_ufs_ativas": (
                f"SELECT SUM(empresas_ativas) AS n, uf FROM {SIMPLES} WHERE NOT is_organizacao_politica "
                "GROUP BY uf ORDER BY n DESC, uf LIMIT 5"
            ),
        }.get(case.id)

    cases = by_id(["ativas_por_porte", "top5_ufs_ativas"])
    results = run(cases, QuestionLLM(sql_for), executor, system)
    assert results["ativas_por_porte"].passed
    # o ranking da gold de teste tem empate; o que se verifica e que colunas trocadas nao reprovam sozinhas
    assert results["top5_ufs_ativas"].reason in ("", "resultado diferente do esperado")


def test_the_caveat_must_reach_the_answer_text(executor, system):
    case = by_id(["ressalva_picos_de_abertura"])
    flagged = (
        "SELECT ano, is_organizacao_politica, SUM(estabelecimentos_abertos) FROM gold_mart_aberturas_mensal "
        "GROUP BY ano, is_organizacao_politica"
    )
    quiet = run(case, QuestionLLM(lambda c: flagged, final="Subiu em 2020."), executor, system)
    aware = run(case, QuestionLLM(lambda c: flagged, final="É ano de eleição municipal."), executor, system)
    assert "não menciona" in quiet["ressalva_picos_de_abertura"].reason
    assert aware["ressalva_picos_de_abertura"].passed


def test_the_evaluation_stops_spending_when_the_budget_runs_out(executor, system):
    class Costly(ReferenceLLM):
        def step(self, system, messages, *, max_tokens):
            turn = super().step(system, messages, max_tokens=max_tokens)
            return LLMTurn(turn.text, turn.tool_calls, turn.content, 1_000_000, 0)

    budget = Budget(limit_usd=0.15, period=MONTH, pricing=Pricing(0.10, 0.50))
    results = evaluate(CASES, month=MONTH, system=system, llm=Costly(), executor=executor, budget=budget)

    assert results[0].passed and results[0].llm_calls == 2
    assert all(r.status == "budget" and r.llm_calls == 0 and not r.passed for r in results[1:])
    assert budget.spent_usd == pytest.approx(0.20)


# --------------------------------------------------------------------------- #
# Metricas e relatorio
# --------------------------------------------------------------------------- #


def result(
    id="a", category="x", passed=True, first=True, calls=2, cost=0.001, elapsed=1.0, reason="", sql=None
):
    return CaseResult(
        id=id, category=category, kind="data", question=f"pergunta {id}?", passed=passed, reason=reason,
        status="answered", llm_calls=calls, queries=1, failed_queries=0 if first else 1, first_query_ok=first,
        input_tokens=100, output_tokens=10, cost_usd=cost, elapsed_s=elapsed, final_sql=sql,
    )  # fmt: skip


def test_summarize_computes_the_metrics():
    results = [
        result("a", "x", True, True, 2, 0.002, 1.0),
        result("b", "x", False, False, 4, 0.004, 3.0, reason="resultado diferente do esperado"),
        result("c", "y", True, True, 3, 0.003, 2.0),
    ]
    results.append(CaseResult(**{**results[0].__dict__, "id": "d", "first_query_ok": None, "category": "y"}))
    s = summarize(results)

    assert (s.cases, s.passed) == (4, 3) and s.accuracy == pytest.approx(0.75)
    assert s.by_category == {"x": {"cases": 2, "passed": 1}, "y": {"cases": 2, "passed": 2}}
    assert s.first_query_ok_rate == pytest.approx(2 / 3)  # quem nao consultou fica fora da conta
    assert s.mean_llm_calls == pytest.approx(2.75)
    assert s.total_cost_usd == pytest.approx(0.011)
    assert s.latency_median_s == pytest.approx(1.5) and s.latency_p95_s == pytest.approx(3.0)
    assert s.failed_queries == 1


def test_summarize_rejects_an_empty_run():
    with pytest.raises(EvaluationError):
        summarize([])


def test_the_report_lists_failures_with_their_sql_and_uses_decimal_commas():
    results = [
        result("certo"),
        result("errado", passed=False, reason="resultado diferente do esperado", sql="SELECT soma FROM t"),
    ]
    text = render_report(results, month=MONTH, label="modelo-x", generated_on="2026-11-15", model="modelo-x")

    assert "1 de 2 (50,0%)" in text
    assert "## Falhas" in text and "SELECT soma FROM t" in text
    assert "resultado diferente do esperado" in text
    assert "0,0020" in text and "0.0020" not in text
    assert "Modelo: `modelo-x`" in text


def test_the_report_without_failures_has_no_failures_section():
    text = render_report([result()], month=MONTH, label="referencia", generated_on="2026-11-15", model=None)
    assert "## Falhas" not in text and "Modo de referência" in text


def test_the_json_has_the_summary_and_no_result_rows():
    payload = json.loads(
        results_json([result()], month=MONTH, label="x", generated_on="2026-11-15", model="m")
    )

    assert payload["summary"]["accuracy"] == 1.0 and payload["model"] == "m"
    assert payload["cases"][0]["id"] == "a"
    assert "rows" not in json.dumps(payload)


# --------------------------------------------------------------------------- #
# Repeticoes e estabilidade
# --------------------------------------------------------------------------- #

WRONG_SQL = f"SELECT SUM(empresas_ativas) FROM {SIMPLES} WHERE uf = 'ZZ'"


class FlakyLLM(QuestionLLM):
    """Acerta 'ativas_sp' nas repeticoes impares e erra nas pares; erra sempre 'top5_ufs_ativas'."""

    def __init__(self) -> None:
        super().__init__(self._pick)
        self.starts: Counter[str] = Counter()

    def _pick(self, case: EvalCase) -> str | None:
        if case.id == "top5_ufs_ativas":
            return WRONG_SQL
        if case.id == "ativas_sp" and self.starts[case.id] % 2 == 0:
            return WRONG_SQL
        return case.reference_sql

    def step(self, system: str, messages: list[dict[str, Any]], *, max_tokens: int) -> LLMTurn:
        if len(messages) == 1:
            self.starts[self._by_question[messages[0]["content"]].id] += 1
        return super().step(system, messages, max_tokens=max_tokens)


FLAKY_IDS = ["ativas_sp", "top5_ufs_ativas", "ativas_por_porte"]


def test_evaluate_runs_each_case_the_requested_number_of_times(executor, system):
    cases = by_id(FLAKY_IDS)
    results = evaluate(cases, month=MONTH, system=system, llm=ReferenceLLM(), executor=executor, repeats=3)

    assert [(r.id, r.run) for r in results] == [(c.id, n) for c in cases for n in (1, 2, 3)]
    once = evaluate(cases, month=MONTH, system=system, llm=ReferenceLLM(), executor=executor)
    assert [r.run for r in once] == [1, 1, 1]


@pytest.mark.parametrize("repeats", [0, -1])
def test_evaluate_rejects_a_repeat_count_below_one(executor, system, repeats):
    with pytest.raises(EvaluationError):
        evaluate(
            by_id(FLAKY_IDS),
            month=MONTH,
            system=system,
            llm=ReferenceLLM(),
            executor=executor,
            repeats=repeats,
        )


def test_stability_separates_stable_unstable_and_always_wrong_cases(executor, system):
    results = evaluate(
        by_id(FLAKY_IDS), month=MONTH, system=system, llm=FlakyLLM(), executor=executor, repeats=3
    )
    by_case = {c.id: c for c in stability(results)}

    stable, flaky, wrong = by_case["ativas_por_porte"], by_case["ativas_sp"], by_case["top5_ufs_ativas"]
    assert (stable.verdict, stable.passes, stable.runs, stable.distinct_sql) == ("estavel", 3, 3, 1)
    assert (flaky.verdict, flaky.passes, flaky.runs, flaky.distinct_sql) == ("instavel", 2, 3, 2)
    assert flaky.reasons == {"resultado diferente do esperado": 1}
    assert (wrong.verdict, wrong.passes, wrong.distinct_sql) == ("sempre errado", 0, 1)
    assert wrong.reasons == {"resultado diferente do esperado": 3}
    assert flaky.pass_rate == pytest.approx(2 / 3) and stable.pass_rate == 1.0


def test_a_single_run_per_case_is_classified_without_ambiguity():
    verdicts = {c.id: c.verdict for c in stability([result("a"), result("b", passed=False, reason="x")])}
    assert verdicts == {"a": "estavel", "b": "sempre errado"}


def repeated(id, outcomes, sql="SELECT 1"):
    return [
        replace(result(id, passed=ok, reason="" if ok else "resultado diferente do esperado", sql=sql), run=n)
        for n, ok in enumerate(outcomes, start=1)
    ]


def test_the_report_with_repeats_has_a_stability_section_instead_of_the_per_case_table():
    results = (
        repeated("firme", [True] * 3)
        + repeated("oscila", [True, False, True])
        + repeated("erra", [False] * 3)
    )
    text = render_report(results, month=MONTH, label="m", generated_on="2026-11-16", model="m")

    assert "Cada caso rodou 3 vezes" in text and "Execuções certas | 5 de 9" in text
    assert "## Estabilidade" in text and "## Casos" not in text
    assert "| firme | 3 de 3 | 1 | estavel | - |" in text
    assert "| oscila | 2 de 3 | 1 | instavel | resultado diferente do esperado (1x) |" in text
    assert "| erra | 0 de 3 | 1 | sempre errado | resultado diferente do esperado (3x) |" in text
    assert text.index("| erra |") < text.index("| oscila |") < text.index("| firme |")  # piores primeiro
    assert "Instáveis (certos em parte das repetições) | 1" in text
    assert "lacuna do prompt" in text and "variação do modelo" in text


def test_the_report_of_a_single_run_keeps_the_per_case_table():
    text = render_report([result()], month=MONTH, label="m", generated_on="2026-11-16", model="m")
    assert "## Casos" in text and "## Estabilidade" not in text and "Casos certos | 1 de 1" in text
    assert "Cada caso rodou" not in text


def test_the_failures_section_shows_each_wrong_sql_once():
    same = repeated("erra", [False] * 3, sql="SELECT errado")
    other = repeated("oscila", [False, False], sql="SELECT a") + repeated("oscila", [True], sql="SELECT b")
    other[2] = replace(other[2], run=3)
    other[1] = replace(other[1], final_sql="SELECT c")
    text = render_report(same + other, month=MONTH, label="m", generated_on="2026-11-16", model="m")

    assert text.count("SELECT errado") == 1
    assert "SELECT a" in text and "SELECT c" in text and "SELECT b" not in text


def test_the_json_has_the_stability_only_when_there_are_repeats():
    many = results_json(
        repeated("a", [True, False]), month=MONTH, label="x", generated_on="2026-11-16", model="m"
    )
    payload = json.loads(many)
    assert payload["repeats"] == 2
    assert payload["stability"] == [
        {
            "id": "a", "category": "x", "runs": 2, "passes": 1, "distinct_sql": 1,
            "reasons": {"resultado diferente do esperado": 1}, "verdict": "instavel", "pass_rate": 0.5,
        }
    ]  # fmt: skip
    assert [c["run"] for c in payload["cases"]] == [1, 2]

    single = json.loads(
        results_json([result()], month=MONTH, label="x", generated_on="2026-11-16", model="m")
    )
    assert "stability" not in single and "repeats" not in single


def test_the_cli_with_repeats_names_the_files_and_prints_the_stability(gold_root, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(
        [
            "--month",
            MONTH,
            "--reference",
            "--repeats",
            "3",
            "--gold-dir",
            str(gold_root),
            "--out-dir",
            str(out),
        ]
    )
    payload = json.loads((out / f"{MONTH}_referencia_x3.json").read_text(encoding="utf-8"))
    total = len(SUITES["dev"])

    assert code == 0 and payload["repeats"] == 3 and payload["summary"]["cases"] == 3 * total
    assert (out / f"{MONTH}_referencia_x3.md").exists() and not (out / f"{MONTH}_referencia.md").exists()
    shown = capsys.readouterr().out
    assert (
        f"{3 * total} de {3 * total} execucoes certas" in shown and f"estaveis {total}, instaveis 0" in shown
    )


@pytest.mark.parametrize("value", ["0", "21", "-2"])
def test_the_cli_rejects_a_repeat_count_out_of_range(gold_root, tmp_path, value, capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--month", MONTH, "--reference", "--repeats", value, "--gold-dir", str(gold_root)])

    assert stopped.value.code == 2 and "--repeats" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def test_the_cli_reference_mode_writes_the_report_and_exits_zero(gold_root, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["--month", MONTH, "--reference", "--gold-dir", str(gold_root), "--out-dir", str(out)])

    assert code == 0
    assert f"{len(CASES)} de {len(CASES)} casos certos" in capsys.readouterr().out
    assert (out / f"{MONTH}_referencia.md").read_text(encoding="utf-8").startswith("# Avaliação do agente")
    payload = json.loads((out / f"{MONTH}_referencia.json").read_text(encoding="utf-8"))
    assert payload["summary"]["accuracy"] == 1.0 and payload["model"] is None


def test_the_cli_runs_the_dev_suite_by_default_and_names_the_others(gold_root, tmp_path, capsys):
    out = tmp_path / "out"
    base = ["--month", MONTH, "--reference", "--gold-dir", str(gold_root), "--out-dir", str(out)]
    assert main(base + ["--suite", "holdout"]) == 0
    assert f"{len(HOLDOUT_CASES)} de {len(HOLDOUT_CASES)} casos certos" in capsys.readouterr().out
    payload = json.loads((out / f"{MONTH}_referencia_holdout.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in payload["cases"]] == [c.id for c in HOLDOUT_CASES]
    assert not (out / f"{MONTH}_referencia.json").exists()  # a suite dev nao foi sobrescrita

    assert main(base + ["--suite", "robustez"]) == 0
    assert f"{len(ROBUSTEZ_CASES)} de {len(ROBUSTEZ_CASES)} casos certos" in capsys.readouterr().out
    payload = json.loads((out / f"{MONTH}_referencia_robustez.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in payload["cases"]] == [c.id for c in ROBUSTEZ_CASES]

    assert main(base + ["--suite", "all"]) == 0
    assert f"{len(ALL_CASES)} de {len(ALL_CASES)} casos certos" in capsys.readouterr().out
    assert (out / f"{MONTH}_referencia_all.md").exists()

    assert main(base) == 0
    assert (out / f"{MONTH}_referencia.md").exists()


def test_the_cli_cases_option_takes_precedence_over_the_suite(gold_root, tmp_path):
    out = tmp_path / "out"
    code = main(
        ["--month", MONTH, "--reference", "--suite", "holdout", "--cases", "ativas_sp",
         "--gold-dir", str(gold_root), "--out-dir", str(out)]
    )  # fmt: skip
    payload = json.loads((out / f"{MONTH}_referencia.json").read_text(encoding="utf-8"))
    assert code == 0 and [c["id"] for c in payload["cases"]] == ["ativas_sp"]


def test_the_cli_can_run_a_subset_of_cases(gold_root, tmp_path):
    out = tmp_path / "out"
    code = main(
        ["--month", MONTH, "--reference", "--cases", "ativas_sp,recusa_fora_do_tema",
         "--gold-dir", str(gold_root), "--out-dir", str(out)]
    )  # fmt: skip
    payload = json.loads((out / f"{MONTH}_referencia.json").read_text(encoding="utf-8"))

    assert code == 0 and [c["id"] for c in payload["cases"]] == ["ativas_sp", "recusa_fora_do_tema"]


def test_the_cli_without_a_key_stops_before_doing_anything(gold_root, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out"
    code = main(["--month", MONTH, "--gold-dir", str(gold_root), "--out-dir", str(out)])

    assert code == 2 and not out.exists()
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_the_cli_exits_nonzero_if_the_reference_mode_has_a_failure(gold_root, tmp_path, monkeypatch):
    broken = (
        EvalCase(
            "ruim", "t", "data", "Pergunta que a referencia erra?", f"SELECT 1 FROM {SIMPLES} WHERE 1 = 0"
        ),
    )
    monkeypatch.setitem(evaluation.SUITES, "dev", broken)
    monkeypatch.setattr(evaluation, "by_id", lambda ids: broken)

    class Wrong(ReferenceLLM):
        def step(self, system, messages, *, max_tokens):
            return LLMTurn("sem consulta", [], [], 0, 0)

    monkeypatch.setattr(evaluation, "ReferenceLLM", lambda: Wrong(broken))
    out = str(tmp_path / "o")
    code = main(
        ["--month", MONTH, "--reference", "--cases", "ruim", "--gold-dir", str(gold_root), "--out-dir", out]
    )
    assert code == 1
