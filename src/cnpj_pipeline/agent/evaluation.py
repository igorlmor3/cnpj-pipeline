"""Avaliacao do agente contra a gold local (DuckDB), com o modelo real ou com o modo de referencia.

    python -m cnpj_pipeline.agent.evaluation --month 2026-10 --reference
    python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1
    python -m cnpj_pipeline.agent.evaluation --month 2026-10 --suite holdout --repeats 5

O modo --reference usa as proprias consultas de referencia no lugar do modelo: valida o conjunto de
perguntas e este programa sem gastar nada. Sem ele, o modelo da Anthropic e chamado (a chave vem de
ANTHROPIC_API_KEY) e o gasto fica limitado por --budget-usd. O relatorio traz so contagens, metricas
e o SQL das consultas; nenhuma linha de resultado.
"""

from __future__ import annotations

import argparse
import hashlib
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
from cnpj_pipeline.agent.eval_stats import bootstrap_interval, wilson_interval
from cnpj_pipeline.agent.evaluation_cases import ALL_CASES, MESES_CASES, SUITES, EvalCase, by_id
from cnpj_pipeline.agent.executor import DuckDBExecutor, Executor, run_query
from cnpj_pipeline.agent.llm import DEFAULT_MODEL, LLMClient, LLMTurn, ToolCall
from cnpj_pipeline.cloud.athena import gold_columns

MAX_PROJECTION_COLUMNS = 8
MAX_REPEATS = 20
ONE_DECIMAL = Decimal("0.1")
REASON_DIFFERENT = "resultado diferente do esperado"
REASON_FORMAT = "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas"


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


def rows_contain(expected: list[list[Any]], got: list[list[Any]]) -> bool:
    """O esperado aparece inteiro entre as linhas do modelo (que devolveu linhas a mais, por exemplo a
    quebra por grupo em vez do grupo pedido)? So diagnostico: nao muda o criterio de acerto."""
    if not expected or not got:
        return False
    width, got_width = len(expected[0]), len(got[0])
    if got_width < width or got_width > MAX_PROJECTION_COLUMNS or len(expected) > len(got):
        return False
    wanted = Counter(tuple(_norm(c) for c in row) for row in expected)
    normalized = [tuple(_norm(c) for c in row) for row in got]
    for picks in permutations(range(got_width), width):
        projected = Counter(tuple(row[i] for i in picks) for row in normalized)
        if not wanted - projected:
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
    run: int = 1  # repeticao (1 a N) quando a avaliacao roda cada caso mais de uma vez


def expected_rows(
    cases: tuple[EvalCase, ...], *, executor: Executor, month: str, months: tuple[str, ...] | None = None
) -> dict[str, list[list[Any]]]:
    """Executa as referencias antes de chamar o modelo. Falha cedo se alguma referencia estiver errada."""
    expected: dict[str, list[list[Any]]] = {}
    for case in cases:
        if not case.reference_sql:
            continue
        try:
            expected[case.id] = run_query(
                case.reference_sql, executor=executor, month=month, months=months
            ).rows
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
            # nos rankings a ordem conta, entao "o valor aparece" nao diz nada sobre o formato
            contained = not case.ordered and rows_contain(expected, ok[-1].rows)
            return REASON_FORMAT if contained else REASON_DIFFERENT
    return ""


def run_case(
    case: EvalCase,
    expected: list[list[Any]] | None,
    *,
    month: str,
    months: tuple[str, ...] | None = None,
    system: str,
    llm: LLMClient,
    executor: Executor,
    budget: Budget | None,
    clock: Callable[[], float] = time.monotonic,
    run: int = 1,
) -> CaseResult:
    started = clock()
    answer = ask(
        case.question, month=month, months=months, system=system, llm=llm, executor=executor, budget=budget
    )
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
        run=run,
    )


def evaluate(
    cases: tuple[EvalCase, ...],
    *,
    month: str,
    months: tuple[str, ...] | None = None,
    system: str,
    llm: LLMClient,
    executor: Executor,
    budget: Budget | None = None,
    clock: Callable[[], float] = time.monotonic,
    repeats: int = 1,
) -> list[CaseResult]:
    """Roda cada caso `repeats` vezes. Os resultados saem na ordem dos casos, com as repeticoes de um
    caso em sequencia; o gasto de todas elas conta no mesmo orcamento."""
    if repeats < 1:
        raise EvaluationError("repeats deve ser pelo menos 1")
    expected = expected_rows(cases, executor=executor, month=month, months=months)
    return [
        run_case(
            c, expected.get(c.id), month=month, months=months, system=system, llm=llm, executor=executor,
            budget=budget, clock=clock, run=n,
        )
        for c in cases
        for n in range(1, repeats + 1)
    ]  # fmt: skip


# --------------------------------------------------------------------------- #
# Modo de referencia: o "modelo" que executa as consultas de referencia
# --------------------------------------------------------------------------- #


class ReferenceLLM:
    """Responde cada pergunta com a consulta de referencia do caso. Nao gasta nada."""

    def __init__(self, cases: tuple[EvalCase, ...] = ALL_CASES + MESES_CASES) -> None:
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
    accuracy_ci_low: float = 0.0  # intervalo de 95% da acuracia (ver accuracy_interval)
    accuracy_ci_high: float = 1.0
    ci_method: str = "wilson"
    questions: int = 0  # perguntas distintas (com repeticoes, menos que `cases`)
    cost_per_correct_usd: float | None = None
    mean_input_tokens: float = 0.0
    mean_output_tokens: float = 0.0
    cost_p95_usd: float = 0.0


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))] if ordered else 0.0


def question_rates(results: list[CaseResult]) -> dict[str, float]:
    """Taxa de acerto por pergunta (1 ou 0 sem repeticoes; fracao das repeticoes com elas)."""
    grouped: dict[str, list[bool]] = {}
    for r in results:
        grouped.setdefault(r.id, []).append(r.passed)
    return {qid: sum(v) / len(v) for qid, v in grouped.items()}


def accuracy_interval(results: list[CaseResult]) -> tuple[float, float, str]:
    """Intervalo de 95% da acuracia. Uma execucao por pergunta: Wilson sobre os acertos. Com
    repeticoes: reamostragem das perguntas, porque repetir uma pergunta nao a torna independente."""
    if not results:
        return (0.0, 1.0, "wilson")
    if max(r.run for r in results) == 1:
        lo, hi = wilson_interval(sum(r.passed for r in results), len(results))
        return (lo, hi, "wilson")
    lo, hi = bootstrap_interval(list(question_rates(results).values()))
    return (lo, hi, "reamostragem das perguntas")


def summarize(results: list[CaseResult]) -> Summary:
    if not results:
        raise EvaluationError("nenhum caso executado")
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.category, {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += int(r.passed)
    queried = [r for r in results if r.first_query_ok is not None]
    ci_low, ci_high, ci_method = accuracy_interval(results)
    passed = sum(r.passed for r in results)
    total_cost = sum(r.cost_usd for r in results)
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
        accuracy_ci_low=ci_low,
        accuracy_ci_high=ci_high,
        ci_method=ci_method,
        questions=len({r.id for r in results}),
        cost_per_correct_usd=(total_cost / passed) if passed else None,
        mean_input_tokens=statistics.fmean(r.input_tokens for r in results),
        mean_output_tokens=statistics.fmean(r.output_tokens for r in results),
        cost_p95_usd=_percentile([r.cost_usd for r in results], 0.95),
    )


@dataclass(frozen=True)
class CaseStability:
    """Como um caso se comportou ao longo das repeticoes."""

    id: str
    category: str
    runs: int
    passes: int
    distinct_sql: int  # quantos SQL finais diferentes o modelo escreveu
    reasons: dict[str, int]  # motivo da falha -> quantas vezes
    verdict: str  # "estavel", "instavel" ou "sempre errado"

    @property
    def pass_rate(self) -> float:
        return self.passes / self.runs


VERDICT_STABLE = "estavel"
VERDICT_UNSTABLE = "instavel"
VERDICT_ALWAYS_WRONG = "sempre errado"


def stability(results: list[CaseResult]) -> list[CaseStability]:
    """Agrupa as repeticoes por caso. Um caso e estavel se acertou em todas, sempre errado se falhou
    em todas e instavel no meio: o primeiro grupo e o segundo apontam o prompt, o terceiro, o modelo."""
    grouped: dict[str, list[CaseResult]] = {}
    for r in results:
        grouped.setdefault(r.id, []).append(r)
    out: list[CaseStability] = []
    for case_id, runs in grouped.items():
        passes = sum(r.passed for r in runs)
        verdict = (
            VERDICT_STABLE
            if passes == len(runs)
            else VERDICT_ALWAYS_WRONG
            if passes == 0
            else VERDICT_UNSTABLE
        )
        out.append(
            CaseStability(
                id=case_id,
                category=runs[0].category,
                runs=len(runs),
                passes=passes,
                distinct_sql=len({r.final_sql for r in runs if r.final_sql}),
                reasons=dict(Counter(r.reason for r in runs if r.reason)),
                verdict=verdict,
            )
        )
    return out


def _num(value: float, digits: int = 1) -> str:
    """Numero com virgula decimal, para o relatorio em portugues."""
    return f"{value:.{digits}f}".replace(".", ",")


def _cost_per_correct(value: float | None) -> str:
    return "-" if value is None else f"US$ {_num(value, 4)}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{_num(value * 100)}%"


def _stability_section(results: list[CaseResult]) -> list[str]:
    cases = stability(results)
    by_verdict = Counter(c.verdict for c in cases)
    out = [
        "",
        "## Estabilidade",
        "",
        "| Classificação | Casos |",
        "|---|---|",
        f"| Estáveis (certos em todas as repetições) | {by_verdict[VERDICT_STABLE]} |",
        f"| Instáveis (certos em parte das repetições) | {by_verdict[VERDICT_UNSTABLE]} |",
        f"| Sempre errados | {by_verdict[VERDICT_ALWAYS_WRONG]} |",
        "",
        "| Caso | Certas | SQL distintos | Classificação | Motivos da falha |",
        "|---|---|---|---|---|",
    ]
    for c in sorted(cases, key=lambda c: (c.verdict == VERDICT_STABLE, c.pass_rate, c.id)):
        reasons = "; ".join(f"{reason} ({n}x)" for reason, n in c.reasons.items()) or "-"
        out.append(f"| {c.id} | {c.passes} de {c.runs} | {c.distinct_sql} | {c.verdict} | {reasons} |")
    return out


def prompt_version(system: str) -> str:
    """Identificador curto do texto enviado ao modelo: cada resultado diz com qual prompt foi gerado."""
    return hashlib.sha256(system.encode("utf-8")).hexdigest()[:12]


def render_report(
    results: list[CaseResult],
    *,
    month: str,
    label: str,
    generated_on: str,
    model: str | None,
    prompt_sha: str | None = None,
) -> str:
    s = summarize(results)
    repeats = max(r.run for r in results)
    correct_label = "Execuções certas" if repeats > 1 else "Casos certos"
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
    ]
    if prompt_sha:
        out += [
            f"Versão do prompt: `{prompt_sha}` (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres)."
        ]
    if repeats > 1:
        out += [f"Cada caso rodou {repeats} vezes, com o mesmo prompt e a mesma gold."]
    out += [
        "",
        "## Resumo",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| {correct_label} | {s.passed} de {s.cases} ({_pct(s.accuracy)}) |",
        f"| Intervalo de 95% da acurácia | {_pct(s.accuracy_ci_low)} a {_pct(s.accuracy_ci_high)} "
        f"({s.ci_method}, {s.questions} perguntas) |",
        f"| Primeira consulta sem erro | {_pct(s.first_query_ok_rate)} |",
        f"| Consultas recusadas ou com erro (total) | {s.failed_queries} |",
        f"| Chamadas ao modelo por pergunta (média) | {_num(s.mean_llm_calls)} |",
        f"| Custo por pergunta (média / p95) | US$ {_num(s.mean_cost_usd, 4)} / "
        f"US$ {_num(s.cost_p95_usd, 4)} |",
        f"| Custo por resposta certa | {_cost_per_correct(s.cost_per_correct_usd)} |",
        f"| Custo total | US$ {_num(s.total_cost_usd, 4)} |",
        f"| Tokens por pergunta (entrada / saída, média) | {_num(s.mean_input_tokens, 0)} / "
        f"{_num(s.mean_output_tokens, 0)} |",
        f"| Latência mediana (p50) / p95 | {_num(s.latency_median_s)} s / {_num(s.latency_p95_s)} s |",
        "",
        "## Por categoria",
        "",
        "| Categoria | Certos | Casos | Intervalo de 95% |",
        "|---|---|---|---|",
    ]
    for cat, b in s.by_category.items():
        lo, hi, _ = accuracy_interval([r for r in results if r.category == cat])
        out.append(f"| {cat} | {b['passed']} | {b['cases']} | {_pct(lo)} a {_pct(hi)} |")
    if repeats > 1:
        out += _stability_section(results)
    else:
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
        shown: set[tuple[str, str | None]] = set()
        for r in failures:
            if (r.id, r.final_sql) in shown:
                continue  # o mesmo SQL errado repetido nao se mostra duas vezes
            shown.add((r.id, r.final_sql))
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
        f'- "{REASON_FORMAT}" separa o erro de formato (o modelo devolveu mais linhas ou colunas do que o '
        "pedido, como a quebra por grupo em vez do grupo) do valor errado. É só diagnóstico: os dois "
        "contam como erro.",
        "- Nas de ressalva, o SQL precisa ter considerado `is_organizacao_politica`.",
        "- Nas de recusa, o certo é terminar sem resposta baseada em consulta.",
        "- O custo usa a tabela de preços informada na execução; o valor cobrado é o da fatura do provedor. "
        "O p95 de latência e de custo, com poucas perguntas, é praticamente o maior valor observado.",
        "- O intervalo de 95% mostra o quanto a acurácia mediria de diferente em outro conjunto de perguntas "
        "do mesmo tipo. Com 25 a 30 perguntas ele tem cerca de 20 pontos percentuais de largura; duas "
        "execuções só diferem de verdade se a comparação pareada (`eval_compare`) disser isso.",
    ]
    if repeats > 1:
        out += [
            "- Estável: acertou em todas as repetições. Sempre errado: errou em todas, o que aponta uma "
            "lacuna do prompt ou do contexto. Instável: acertou em umas e errou em outras, o que aponta "
            "variação do modelo.",
            "- Com poucas repetições, um caso instável pode parecer estável por acaso; quanto mais "
            "repetições, mais confiável a classificação.",
        ]
    out.append("")
    return "\n".join(out)


def results_json(
    results: list[CaseResult],
    *,
    month: str,
    label: str,
    generated_on: str,
    model: str | None,
    prompt_sha: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "month": month,
        "label": label,
        "model": model,
        "generated_on": generated_on,
        "prompt_sha": prompt_sha,
        "summary": asdict(summarize(results)),
        "cases": [asdict(r) for r in results],
    }
    if max(r.run for r in results) > 1:
        payload["repeats"] = max(r.run for r in results)
        payload["stability"] = [asdict(c) | {"pass_rate": c.pass_rate} for c in stability(results)]
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avalia o agente de texto para SQL contra a gold local.")
    parser.add_argument("--month", required=True, help="mes de referencia (o padrao do agente), AAAA-MM")
    parser.add_argument(
        "--months",
        help="meses que o agente pode consultar, AAAA-MM separados por virgula, incluindo --month; "
        "sem isso o agente enxerga so --month",
    )
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
    parser.add_argument(
        "--suite",
        choices=sorted(SUITES),
        default="dev",
        help="conjunto de casos: dev (ajuste do prompt), holdout, robustez (frases informais, contas e "
        "armadilhas), validacao (usado no ajuste de multiplos meses, agora de desenvolvimento), "
        "reservado2 (reservado atual), meses (varios meses, exige "
        "--months) ou all",
    )
    parser.add_argument("--cases", help="ids separados por virgula; tem prioridade sobre --suite")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help=f"vezes que cada caso roda (1 a {MAX_REPEATS}); com mais de 1 o relatorio traz a estabilidade",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= MAX_REPEATS:
        parser.error(f"--repeats deve ficar entre 1 e {MAX_REPEATS}")

    months = tuple(sorted({m.strip() for m in args.months.split(",") if m.strip()})) if args.months else None
    if months and args.month not in months:
        parser.error("--months precisa incluir --month")
    cases = by_id(args.cases.split(",")) if args.cases else SUITES[args.suite]
    if any(c.needs_months for c in cases) and not months:
        parser.error("os casos de varios meses precisam de --months")
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
    if args.suite != "dev" and not args.cases:
        label = f"{label}_{args.suite}"

    columns = gold_columns(args.gold_dir, args.month, list(TABLES))
    system = build_system_prompt(columns, month=args.month, months=months)
    executor = DuckDBExecutor(args.gold_dir)
    try:
        results = evaluate(
            cases, month=args.month, months=months, system=system, llm=llm, executor=executor,
            budget=budget, repeats=args.repeats,
        )  # fmt: skip
    finally:
        executor.close()

    generated_on = datetime.now(UTC).date().isoformat()
    fields = {
        "month": args.month,
        "label": label,
        "generated_on": generated_on,
        "model": model,
        "prompt_sha": prompt_version(system),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.month}_{label}" + (f"_x{args.repeats}" if args.repeats > 1 else "")
    (args.out_dir / f"{stem}.md").write_text(render_report(results, **fields), encoding="utf-8")
    (args.out_dir / f"{stem}.json").write_text(results_json(results, **fields), encoding="utf-8")
    summary = summarize(results)
    if args.repeats > 1:
        verdicts = Counter(c.verdict for c in stability(results))
        print(
            f"{summary.passed} de {summary.cases} execucoes certas ({_pct(summary.accuracy)}); "
            f"estaveis {verdicts[VERDICT_STABLE]}, instaveis {verdicts[VERDICT_UNSTABLE]}, "
            f"sempre errados {verdicts[VERDICT_ALWAYS_WRONG]}; "
            f"custo total US$ {_num(summary.total_cost_usd, 4)}"
        )
    else:
        print(
            f"{summary.passed} de {summary.cases} casos certos ({_pct(summary.accuracy)}); "
            f"custo total US$ {_num(summary.total_cost_usd, 4)}"
        )
    print(f"relatorio em {args.out_dir / (stem + '.md')}")
    return 0 if (not args.reference or summary.passed == summary.cases) else 1


if __name__ == "__main__":
    sys.exit(main())
