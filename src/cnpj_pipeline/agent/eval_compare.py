"""Compara duas execucoes da avaliacao (dois prompts, dois modelos ou antes e depois de uma mudanca).

    python -m cnpj_pipeline.agent.eval_compare docs/agent_eval/a.json docs/agent_eval/b.json
    python -m cnpj_pipeline.agent.eval_compare a.json b.json --out docs/agent_eval/comparacao.md

A comparacao e pareada: pergunta a pergunta, sobre as que existem nas duas. Com uma execucao por
pergunta usa o teste exato de McNemar (conta so as perguntas em que uma acertou e a outra errou);
com repeticoes, o intervalo da diferenca das taxas de acerto, reamostrando as perguntas. Nao chama
modelo e nao le a gold: so os JSON que a avaliacao escreveu.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cnpj_pipeline.agent.eval_stats import exact_mcnemar_p, paired_difference_interval
from cnpj_pipeline.agent.evaluation import (
    CaseResult,
    EvaluationError,
    Summary,
    _cost_per_correct,
    _num,
    _pct,
    question_rates,
    summarize,
)

ALPHA = 0.05


def load_run(path: Path) -> tuple[dict[str, Any], list[CaseResult]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fields = set(CaseResult.__dataclass_fields__)
    results = [CaseResult(**{k: v for k, v in case.items() if k in fields}) for case in payload["cases"]]
    if not results:
        raise EvaluationError(f"{path} nao tem casos")
    return payload, results


def _label(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("model") or payload.get("label") or "?")]
    if payload.get("prompt_sha"):
        parts.append(f"prompt {payload['prompt_sha']}")
    return ", ".join(parts)


def _row(name: str, payload: dict[str, Any], s: Summary) -> str:
    return (
        f"| {name}: {_label(payload)} | {_pct(s.accuracy)} ({_pct(s.accuracy_ci_low)} a "
        f"{_pct(s.accuracy_ci_high)}) | US$ {_num(s.mean_cost_usd, 4)} "
        f"| {_cost_per_correct(s.cost_per_correct_usd)} "
        f"| {_num(s.latency_median_s)} s / {_num(s.latency_p95_s)} s |"
    )


def compare(
    payload_a: dict[str, Any],
    results_a: list[CaseResult],
    payload_b: dict[str, Any],
    results_b: list[CaseResult],
) -> str:
    common = sorted({r.id for r in results_a} & {r.id for r in results_b})
    if not common:
        raise EvaluationError("as duas execucoes nao tem perguntas em comum")
    keep = set(common)
    a = [r for r in results_a if r.id in keep]
    b = [r for r in results_b if r.id in keep]
    sa, sb = summarize(a), summarize(b)
    rates_a, rates_b = question_rates(a), question_rates(b)
    only_a = [q for q in common if rates_a[q] > rates_b[q]]
    only_b = [q for q in common if rates_b[q] > rates_a[q]]
    repeated = max(r.run for r in a + b) > 1
    diff = sum(rates_b[q] - rates_a[q] for q in common) / len(common)

    out = [
        "# Comparação de duas execuções da avaliação",
        "",
        f"{len(common)} perguntas em comum"
        + (
            f" (A tem {len({r.id for r in results_a}) - len(common)} a mais, "
            f"B tem {len({r.id for r in results_b}) - len(common)} a mais)."
            if len({r.id for r in results_a}) != len(common) or len({r.id for r in results_b}) != len(common)
            else "."
        ),
        "",
        "| Execução | Acurácia (intervalo de 95%) | Custo por pergunta | Custo por resposta certa "
        "| Latência p50 / p95 |",
        "|---|---|---|---|---|",
        _row("A", payload_a, sa),
        _row("B", payload_b, sb),
        "",
        "## Diferença pareada (B menos A)",
        "",
    ]
    if repeated:
        lo, hi = paired_difference_interval([rates_a[q] for q in common], [rates_b[q] for q in common])
        out += [
            f"Diferença média da taxa de acerto: {diff * 100:+.1f} pontos percentuais "
            f"(intervalo de 95%: {lo * 100:+.1f} a {hi * 100:+.1f}, "
            f"reamostrando as {len(common)} perguntas).",
            "",
            f"Perguntas com taxa maior em A: {len(only_a)}. Com taxa maior em B: {len(only_b)}.",
        ]
        significant = not lo <= 0 <= hi
    else:
        p = exact_mcnemar_p(len(only_a), len(only_b))
        both_right = sum(rates_a[q] == rates_b[q] == 1 for q in common)
        both_wrong = sum(rates_a[q] == rates_b[q] == 0 for q in common)
        out += [
            "| | B certo | B errado |",
            "|---|---|---|",
            f"| **A certo** | {both_right} | {len(only_a)} |",
            f"| **A errado** | {len(only_b)} | {both_wrong} |",
            "",
            f"Diferença: {diff * 100:+.1f} pontos percentuais. Teste exato de McNemar sobre as "
            f"{len(only_a) + len(only_b)} perguntas em que as execuções discordam: p = {p:.3f}.",
        ]
        significant = p < ALPHA
    out += [
        "",
        (
            "A diferença é maior do que o acaso explicaria, no nível de 5%."
            if significant
            else "A diferença não é distinguível do acaso com este número de perguntas. Isso não prova "
            "que as execuções são iguais: com poucas perguntas, só diferenças grandes aparecem."
        ),
    ]
    if only_a or only_b:
        out += ["", "## Perguntas que mudaram", ""]
        out += [f"- {q}: A {rates_a[q] * 100:.0f}% e B {rates_b[q] * 100:.0f}%" for q in only_a + only_b]
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara duas execucoes da avaliacao do agente.")
    parser.add_argument("run_a", type=Path, help="JSON da primeira execucao (a base)")
    parser.add_argument("run_b", type=Path, help="JSON da segunda execucao")
    parser.add_argument("--out", type=Path, help="grava o relatorio em Markdown neste arquivo")
    args = parser.parse_args(argv)
    try:
        payload_a, results_a = load_run(args.run_a)
        payload_b, results_b = load_run(args.run_b)
        report = compare(payload_a, results_a, payload_b, results_b)
    except (OSError, KeyError, ValueError, EvaluationError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"comparacao em {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
