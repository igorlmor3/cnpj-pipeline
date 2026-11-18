"""Grava respostas reais do agente como exemplos fixos para o site.

    python -m cnpj_pipeline.agent.record_examples --month 2026-10

O site mostra esses exemplos sem chamar nenhuma API: a pergunta, a resposta, o SQL e o resultado. As
perguntas rodam de verdade contra a gold local, com o modelo, e o custo total fica na casa de centavos.
So o que sai como esperado e gravado: as perguntas de dados precisam terminar com resposta, e a pergunta
fora do escopo precisa terminar em recusa. Se qualquer uma sair diferente, nada e gravado.
A saida nao tem data nem hora.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cnpj_pipeline.agent.ask import Answer, QueryRecord, ask, build_system_prompt
from cnpj_pipeline.agent.budget import HAIKU_5_5, Budget, Pricing
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.executor import DuckDBExecutor, Executor
from cnpj_pipeline.agent.llm import DEFAULT_MODEL, LLMClient
from cnpj_pipeline.agent.trace import build_trace
from cnpj_pipeline.site_export import AGENT_EXAMPLES_FILE

SCHEMA_VERSION = 1
MAX_ROWS = 40


@dataclass(frozen=True)
class Example:
    id: str
    question: str
    expected_status: str = "answered"
    needs_months: bool = False  # a pergunta compara meses: so e gravada com mais de um mes


EXAMPLES: tuple[Example, ...] = (
    Example("ranking_ufs", "Quais são as 5 UFs com mais empresas ativas?"),
    Example(
        "mei_por_uf",
        "Em quais UFs a participação de MEI entre as empresas ativas é maior? Mostre as 5 primeiras.",
    ),
    Example("aberturas_por_ano", "Quantos estabelecimentos foram abertos por ano de 2020 a 2025?"),
    Example(
        "natureza_mais_comum",
        "Qual é a natureza jurídica mais comum entre as empresas ativas e qual a participação dela?",
    ),
    Example(
        "mei_ao_longo_dos_meses",
        "Como evoluiu o número de empresas ativas optantes pelo MEI, mês a mês, no Brasil?",
        needs_months=True,
    ),
    Example(
        "ufs_que_mais_ganharam",
        "Quais UFs mais ganharam empresas ativas do primeiro ao último mês? Mostre as 5 primeiras.",
        needs_months=True,
    ),
    Example(
        "fora_do_escopo",
        "Qual é a razão social e o endereço do CNPJ 00.000.000/0001-91?",
        expected_status="out_of_scope",
    ),
)


def last_result(answer: Answer) -> QueryRecord | None:
    return next((q for q in reversed(answer.queries) if q.ok), None)


def example_entry(example: Example, answer: Answer) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": example.id,
        "pergunta": example.question,
        "status": answer.status,
        "resposta": answer.text,
    }
    record = last_result(answer) if answer.status == "answered" else None
    if record is not None:
        entry["sql"] = record.sql
        entry["colunas"] = record.columns
        entry["linhas"] = record.rows[:MAX_ROWS]
        entry["truncado"] = record.may_be_truncated or len(record.rows) > MAX_ROWS
    entry["trajeto"] = build_trace(example.question, answer, lang="pt")
    return entry


class UnexpectedOutcome(RuntimeError):
    """Uma pergunta nao terminou como o esperado; nada deve ser gravado."""


def record(
    month: str,
    *,
    months: tuple[str, ...] | None = None,
    llm: LLMClient,
    executor: Executor,
    system: str,
    budget: Budget,
    model: str,
    examples: tuple[Example, ...] = EXAMPLES,
) -> dict[str, Any]:
    entries = []
    for example in examples:
        if example.needs_months and not (months and len(months) > 1):
            continue
        answer = ask(
            example.question,
            month=month,
            months=months,
            system=system,
            llm=llm,
            executor=executor,
            budget=budget,
        )
        if answer.status != example.expected_status:
            raise UnexpectedOutcome(
                f"a pergunta '{example.id}' terminou em '{answer.status}', "
                f"e o esperado era '{example.expected_status}'"
            )
        entry = example_entry(example, answer)
        if example.expected_status == "answered" and "linhas" not in entry:
            raise UnexpectedOutcome(f"a pergunta '{example.id}' respondeu sem nenhum resultado de consulta")
        entries.append(entry)
    document: dict[str, Any] = {
        "esquema": SCHEMA_VERSION,
        "mes_referencia": month,
        "modelo": model,
        "exemplos": entries,
    }
    if months and len(months) > 1:
        document["meses"] = list(months)
    return document


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=1) + "\n"


def published_months(option: str | None, index: Path) -> tuple[str, ...] | None:
    """Meses que o agente consulta: a opcao, ou a lista de months.json; None quando ha so um."""
    if option:
        found = {m.strip() for m in option.split(",") if m.strip()}
    elif index.exists():
        found = set(json.loads(index.read_text(encoding="utf-8")).get("meses", []))
    else:
        return None
    return tuple(sorted(found)) if len(found) > 1 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grava respostas reais do agente como exemplos do site.")
    parser.add_argument("--month", required=True, help="mes de referencia, AAAA-MM")
    parser.add_argument("--gold-dir", type=Path, default=Path("data") / "gold")
    parser.add_argument("--out-dir", type=Path, default=Path("site") / "public" / "data")
    parser.add_argument(
        "--months",
        help="meses que o agente pode consultar, AAAA-MM separados por virgula; sem isso, usa os de "
        "months.json do site",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget-usd", type=float, default=0.5, help="teto de gasto desta gravacao")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Defina a variavel de ambiente ANTHROPIC_API_KEY (a chave nao e lida de arquivo).",
            file=sys.stderr,
        )
        return 2
    target = args.out_dir / args.month
    if not (target / "index.json").exists():
        print(
            f"Rode antes: python -m cnpj_pipeline.site_export --month {args.month}",
            file=sys.stderr,
        )
        return 1

    months = published_months(args.months, args.out_dir / "months.json")
    if months and args.month not in months:
        print("--months precisa incluir --month.", file=sys.stderr)
        return 1

    from cnpj_pipeline.agent.llm import AnthropicClient
    from cnpj_pipeline.cloud.athena import gold_columns

    llm = AnthropicClient(model=args.model)
    budget = Budget(
        limit_usd=args.budget_usd,
        period=args.month,
        pricing=Pricing(HAIKU_5_5.input_per_mtok, HAIKU_5_5.output_per_mtok),
    )
    columns = gold_columns(args.gold_dir, args.month, list(TABLES))
    system = build_system_prompt(columns, month=args.month, months=months)
    executor = DuckDBExecutor(args.gold_dir)
    try:
        document = record(
            args.month, months=months, llm=llm, executor=executor, system=system, budget=budget,
            model=args.model,
        )  # fmt: skip
    except UnexpectedOutcome as exc:
        print(f"Nada foi gravado: {exc}.", file=sys.stderr)
        return 1
    finally:
        executor.close()

    path = target / AGENT_EXAMPLES_FILE
    path.write_text(render(document), encoding="utf-8", newline="\n")
    print(f"{len(document['exemplos'])} exemplos gravados em {path}; custo US$ {budget.spent_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
