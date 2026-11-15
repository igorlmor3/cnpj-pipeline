"""Avaliacao do agente contra a gold local (DuckDB), com o modelo real ou com o modo de referencia.

    python -m cnpj_pipeline.agent.evaluation --month 2026-10 --reference
    python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1

O modo --reference usa as proprias consultas de referencia no lugar do modelo: valida o conjunto de
perguntas e este programa sem gastar nada. Sem ele, o modelo da Anthropic e chamado (a chave vem de
ANTHROPIC_API_KEY) e o gasto fica limitado por --budget-usd. O relatorio traz so contagens, metricas
e o SQL das consultas; nenhuma linha de resultado.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import permutations
from pathlib import Path
from typing import Any

from cnpj_pipeline.agent.ask import Answer, ask, build_system_prompt
from cnpj_pipeline.agent.budget import HAIKU_5_5, Budget, Pricing
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.evaluation_cases import CASES, EvalCase, by_id
from cnpj_pipeline.agent.executor import DuckDBExecutor, Executor, run_query
from cnpj_pipeline.agent.llm import DEFAULT_MODEL, LLMClient, LLMTurn, ToolCall
from cnpj_pipeline.cloud.athena import gold_columns

MAX_PROJECTION_COLUMNS = 8
ONE_DECIMAL = Decimal("0.1")


class EvaluationError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Comparacao de resultados
# --------------------------------------------------------------------------- #


def _norm(value: Any) -> Any:
    """Normaliza uma celula: inteiros exatos, numeros nao inteiros com 1 casa, texto sem caixa."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal) or (
        isinstance(value, str) and re.fullmatch(r"-?\d+\.\d+", value.strip())
    ):
        number = Decimal(str(value).strip())
        if not number.is_finite():
            return str(value)
        if number == number.to_integral_value():
            return int(number)
        return number.quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)
    return str(value).strip().casefold()


def rows_match(expected: list[list[Any]], got: list[list[Any]], *, ordered: bool) -> bool:
    """O resultado do modelo bate com o esperado? Aceita colunas a mais ou em outra ordem (o esperado
    precisa aparecer como uma projecao das colunas do modelo). Sem `ordered`, a ordem das linhas nao conta."""
    if not expected or not got:
        return not expected and not got
    width, got_width = len(expected[0]), len(got[0])
    if got_width < width or got_width > MAX_PROJECTION_COLUMNS or len(expected) != len(got):
        return False
    wanted = [tuple(_norm(c) for c in row) for row in expected]
    normalized = [tuple(_norm(c) for c in row) for row in got]
    for picks in permutations(range(got_width), width):
        projected = [tuple(row[i] for i in picks) for row in normalized]
        if projected == wanted if ordered else Counter(projected) == Counter(wanted):
            return True
    return False


# --------------------------------------------------------------------------- #
# Execucao dos casos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    kind: str
    question: str
    passed: bool
    reason: str  # vazio quando passou
    status: str
    llm_calls: int
    queries: int
    failed_queries: int
    first_query_ok: bool | None  # None se o modelo nao consultou
    input_tokens: int
    output_tokens: int
    cost_usd: float
    elapsed_s: float
    final_sql: str | None = None


def expected_rows(
    cases: tuple[EvalCase, ...], *, executor: Executor, month: str
) -> dict[str, list[list[Any]]]:
    """Executa as referencias antes de chamar o modelo. Falha cedo se alguma referencia estiver errada."""
    expected: dict[str, list[list[Any]]] = {}
    for case in cases:
        if not case.reference_sql:
            continue
        try:
            expected[case.id] = run_query(case.reference_sql, executor=executor, month=month).rows
        except Exception as exc:
            raise EvaluationError(f"a referencia de '{case.id}' falhou: {exc}") from exc
    return expected


def judge(case: EvalCase, answer: Answer, expected: list[list[Any]] | None) -> str:
    """Devolve o motivo da falha, ou texto vazio se o caso passou."""
    if case.kind == "refusal":
        return "" if answer.status == "out_of_scope" else f"deveria recusar, mas o estado foi {answer.status}"
    if answer.status != "answered":
        return f"estado {answer.status} em vez de answered"
    ok = [q for q in answer.queries if q.ok]
    if not ok:
        return "nenhuma consulta bem-sucedida"
    joined_sql = " ".join(q.sql for q in ok).casefold()
    for piece in case.must_contain:
        if piece.casefold() not in joined_sql:
            return f"o SQL não usou {piece}"
    for piece in case.answer_must_mention:
        if piece.casefold() not in answer.text.casefold():
            return f"a resposta não menciona '{piece}'"
    if case.compares_result and expected is not None:
        if not rows_match(expected, ok[-1].rows, ordered=case.ordered):
            return "resultado diferente do esperado"
    return ""


def run_case(
    case: EvalCase,
    expected: list[list[Any]] | None,
    *,
    month: str,
    system: str,
    llm: LLMClient,
    executor: Executor,
    budget: Budget | None,
    clock: Callable[[], float] = time.monotonic,
) -> CaseResult:
    started = clock()
    answer = ask(case.question, month=month, system=system, llm=llm, executor=executor, budget=budget)
    elapsed = clock() - started
    reason = judge(case, answer, expected)
    ok = [q for q in answer.queries if q.ok]
    return CaseResult(
        id=case.id,
        category=case.category,
        kind=case.kind,
        question=case.question,
        passed=not reason,
        reason=reason,
        status=answer.status,
        llm_calls=answer.llm_calls,
        queries=len(answer.queries),
        failed_queries=sum(1 for q in answer.queries if not q.ok),
        first_query_ok=answer.queries[0].ok if answer.queries else None,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        cost_usd=answer.cost_usd,
        elapsed_s=elapsed,
        final_sql=ok[-1].sql if ok else None,
    )


def evaluate(
    cases: tuple[EvalCase, ...],
    *,
    month: str,
    system: str,
    llm: LLMClient,
    executor: Executor,
    budget: Budget | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[CaseResult]:
    expected = expected_rows(cases, executor=executor, month=month)
    return [
        run_case(
            c, expected.get(c.id), month=month, system=system, llm=llm, executor=executor,
            budget=budget, clock=clock,
        )
        for c in cases
    ]  # fmt: skip


# --------------------------------------------------------------------------- #
# Modo de referencia: o "modelo" que executa as consultas de referencia
# --------------------------------------------------------------------------- #


class ReferenceLLM:
    """Responde cada pergunta com a consulta de referencia do caso. Nao gasta nada."""

    def __init__(self, cases: tuple[EvalCase, ...] = CASES) -> None:
        self._by_question = {c.question: c for c in cases}

    def step(self, system: str, messages: list[dict[str, Any]], *, max_tokens: int) -> LLMTurn:
        case = self._by_question[messages[0]["content"]]
        if len(messages) == 1 and case.reference_sql:
            call = ToolCall(id="ref", sql=case.reference_sql)
            content = [{"type": "tool_use", "id": "ref", "name": "run_sql", "input": {"sql": call.sql}}]
            return LLMTurn("", [call], content, 0, 0)
        text = case.reference_answer or (
            "Resultado conforme a referência." if case.reference_sql else "Sem dados."
        )
        return LLMTurn(text, [], [{"type": "text", "text": text}], 0, 0)


# --------------------------------------------------------------------------- #
# Metricas e relatorio
# --------------------------------------------------------------------------- #


@dataclass
class Summary:
    cases: int
    passed: int
    accuracy: float
    by_category: dict[str, dict[str, int]]
    first_query_ok_rate: float | None
    mean_llm_calls: float
    mean_cost_usd: float
    total_cost_usd: float
    latency_median_s: float
    latency_p95_s: float
    failed_queries: int
    extra: dict[str, Any] = field(default_factory=dict)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))] if ordered else 0.0


def summarize(results: list[CaseResult]) -> Summary:
    if not results:
        raise EvaluationError("nenhum caso executado")
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.category, {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += int(r.passed)
    queried = [r for r in results if r.first_query_ok is not None]
    return Summary(
        cases=len(results),
        passed=sum(r.passed for r in results),
        accuracy=sum(r.passed for r in results) / len(results),
        by_category=by_category,
        first_query_ok_rate=(sum(bool(r.first_query_ok) for r in queried) / len(queried))
        if queried
        else None,
        mean_llm_calls=statistics.fmean(r.llm_calls for r in results),
        mean_cost_usd=statistics.fmean(r.cost_usd for r in results),
        total_cost_usd=sum(r.cost_usd for r in results),
        latency_median_s=statistics.median(r.elapsed_s for r in results),
        latency_p95_s=_percentile([r.elapsed_s for r in results], 0.95),
        failed_queries=sum(r.failed_queries for r in results),
    )


def _num(value: float, digits: int = 1) -> str:
    """Numero com virgula decimal, para o relatorio em portugues."""
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{_num(value * 100)}%"


def render_report(
    results: list[CaseResult], *, month: str, label: str, generated_on: str, model: str | None
) -> str:
    s = summarize(results)
    out = [
        f"# Avaliação do agente: {label}, mês {month}",
        "",
        f"Gerado em {generated_on}. "
        + (
            f"Modelo: `{model}`."
            if model
            else "Modo de referência: as consultas de referência no lugar do modelo."
        ),
        "O relatório traz contagens, métricas e SQL; nenhuma linha de resultado.",
        "",
        "## Resumo",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Casos certos | {s.passed} de {s.cases} ({_pct(s.accuracy)}) |",
        f"| Primeira consulta sem erro | {_pct(s.first_query_ok_rate)} |",
        f"| Consultas recusadas ou com erro (total) | {s.failed_queries} |",
        f"| Chamadas ao modelo por pergunta (média) | {_num(s.mean_llm_calls)} |",
        f"| Custo por pergunta (média) | US$ {_num(s.mean_cost_usd, 4)} |",
        f"| Custo total | US$ {_num(s.total_cost_usd, 4)} |",
        f"| Latência mediana / p95 | {_num(s.latency_median_s)} s / {_num(s.latency_p95_s)} s |",
        "",
        "## Por categoria",
        "",
        "| Categoria | Certos | Casos |",
        "|---|---|---|",
    ]
    out += [f"| {cat} | {b['passed']} | {b['cases']} |" for cat, b in s.by_category.items()]
    out += [
        "",
        "## Casos",
        "",
        "| Caso | Resultado | Chamadas | Consultas | Custo (US$) | Motivo da falha |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        out.append(
            f"| {r.id} | {'certo' if r.passed else 'errado'} | {r.llm_calls} | {r.queries} "
            f"| {_num(r.cost_usd, 4)} | {r.reason or '-'} |"
        )
    failures = [r for r in results if not r.passed]
    if failures:
        out += ["", "## Falhas", ""]
        for r in failures:
            out += [f"**{r.id}** ({r.status}): {r.question}", ""]
            if r.final_sql:
                out += ["```sql", r.final_sql, "```", ""]
    out += [
        "",
        "## Como ler",
        "",
        "- Nas perguntas de dados o resultado da última consulta bem-sucedida é comparado com o da "
        "consulta de referência: inteiros exatos, números não inteiros com 1 casa decimal, texto sem "
        "diferença de caixa, colunas a mais ou em outra ordem aceitas. Em perguntas com vários grupos "
        "a referência traz só os números, então o rótulo de cada grupo não é conferido.",
        "- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.",
        "- Nas de recusa, o certo é terminar sem resposta baseada em consulta.",
        "- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor.",
        "",
    ]
    return "\n".join(out)


def results_json(
    results: list[CaseResult], *, month: str, label: str, generated_on: str, model: str | None
) -> str:
    payload = {
        "month": month,
        "label": label,
        "model": model,
        "generated_on": generated_on,
        "summary": asdict(summarize(results)),
        "cases": [asdict(r) for r in results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avalia o agente de texto para SQL contra a gold local.")
    parser.add_argument("--month", required=True, help="mes de referencia, AAAA-MM")
    parser.add_argument("--gold-dir", type=Path, default=Path("data/gold"))
    parser.add_argument("--out-dir", type=Path, default=Path("docs/agent_eval"))
    parser.add_argument(
        "--reference", action="store_true", help="usa as consultas de referencia; nao chama o modelo"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget-usd", type=float, default=1.0, help="teto de gasto desta avaliacao")
    parser.add_argument(
        "--price-in", type=float, default=HAIKU_5_5.input_per_mtok, help="US$ por milhao de tokens de entrada"
    )
    parser.add_argument(
        "--price-out", type=float, default=HAIKU_5_5.output_per_mtok, help="US$ por milhao de tokens de saida"
    )
    parser.add_argument("--cases", help="ids separados por virgula; padrao: todos")
    args = parser.parse_args(argv)

    cases = by_id(args.cases.split(",")) if args.cases else CASES
    llm: LLMClient
    budget: Budget | None
    if args.reference:
        llm, budget, label, model = ReferenceLLM(), None, "referencia", None
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "Defina a variavel de ambiente ANTHROPIC_API_KEY (a chave nao e lida de arquivo).",
                file=sys.stderr,
            )
            return 2
        from cnpj_pipeline.agent.llm import AnthropicClient

        llm = AnthropicClient(model=args.model)
        budget = Budget(
            limit_usd=args.budget_usd, period=args.month, pricing=Pricing(args.price_in, args.price_out)
        )
        label, model = args.model, args.model

    columns = gold_columns(args.gold_dir, args.month, list(TABLES))
    system = build_system_prompt(columns, month=args.month)
    executor = DuckDBExecutor(args.gold_dir)
    try:
        results = evaluate(cases, month=args.month, system=system, llm=llm, executor=executor, budget=budget)
    finally:
        executor.close()

    generated_on = datetime.now(UTC).date().isoformat()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.month}_{label}"
    (args.out_dir / f"{stem}.md").write_text(
        render_report(results, month=args.month, label=label, generated_on=generated_on, model=model),
        encoding="utf-8",
    )
    (args.out_dir / f"{stem}.json").write_text(
        results_json(results, month=args.month, label=label, generated_on=generated_on, model=model),
        encoding="utf-8",
    )
    summary = summarize(results)
    print(
        f"{summary.passed} de {summary.cases} casos certos ({_pct(summary.accuracy)}); "
        f"custo total US$ {_num(summary.total_cost_usd, 4)}"
    )
    print(f"relatorio em {args.out_dir / (stem + '.md')}")
    return 0 if (not args.reference or summary.passed == summary.cases) else 1


if __name__ == "__main__":
    sys.exit(main())
