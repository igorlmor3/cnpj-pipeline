"""Portao da avaliacao do agente: o que se confere a cada mudanca, sem modelo e sem custo.

Uma avaliacao com o modelo real custa dinheiro e varia de uma execucao para outra, entao ela nao
roda no CI. O que o CI pode conferir, e que costuma quebrar sem ninguem perceber, e a parte
deterministica. O comando `check` faz quatro verificacoes:

1. Referencias. Cada pergunta dos conjuntos traz uma consulta de referencia; ela precisa passar
   pela guarda atual, executar na gold e ser julgada certa. Pega uma regra da guarda que barra uma
   consulta legitima, uma coluna que mudou de nome e um julgamento quebrado.
2. Prompt. A base (abaixo) guarda o hash do prompt com que o modelo foi medido. Se o texto que vai
   ao modelo mudou desde entao (contexto das tabelas, regras de resposta), a medicao nao vale mais
   e o portao falha ate alguem medir de novo.
3. Conjuntos. As perguntas de cada conjunto medido precisam ser as mesmas da base: acrescentar ou
   tirar uma pergunta muda o que o numero significa.
4. Consultas medidas. Todo SQL final que o modelo escreveu na medicao precisa passar na guarda de
   hoje. Se uma regra nova barraria algo que o modelo usava, o numero da base ja nao descreve o
   agente.

A base (`docs/agent_eval/baseline.json`) aponta para os JSON de uma execucao real de cada conjunto,
com o hash SHA-256 de cada arquivo, e nao copia nada deles. O comando `compare` coloca uma execucao
nova ao lado da base e falha so se a queda de acerto for maior do que o acaso explicaria; com poucas
perguntas isso detecta quedas grandes, e uma queda pequena aparece no relatorio sem derrubar nada.

    python -m cnpj_pipeline.agent.eval_gate check --gold-dir data/gold
    python -m cnpj_pipeline.agent.eval_gate baseline --run dev=<json> --run holdout=<json> ...
    python -m cnpj_pipeline.agent.eval_gate compare --suite holdout nova_execucao.json

No CI a gold vem da demonstracao sintetica (`python -m cnpj_pipeline.demo`): o prompt depende so das
colunas e do mes, nao dos dados, e o hash sai igual ao da gold real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from cnpj_pipeline.agent.ask import build_system_prompt
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.eval_compare import compare, load_run, paired
from cnpj_pipeline.agent.evaluation import (
    CaseResult,
    EvaluationError,
    ReferenceLLM,
    evaluate,
    prompt_version,
    summarize,
)
from cnpj_pipeline.agent.evaluation_cases import SUITES, EvalCase
from cnpj_pipeline.agent.executor import DuckDBExecutor
from cnpj_pipeline.agent.sql_guard import SqlRejected, guard
from cnpj_pipeline.cloud.athena import gold_columns

BASELINE_VERSION = 1
DEFAULT_BASELINE = Path("docs/agent_eval/baseline.json")
DEFAULT_MONTH = "2026-10"
MEASURED_SUITES = ("dev", "holdout", "robustez", "validacao", "reservado2", "meses")
MAX_LISTED = 5

OK, ERROR, NOTE = "ok", "erro", "aviso"


@dataclass(frozen=True)
class Finding:
    level: str  # ok, erro ou aviso
    check: str  # referencias, prompt, conjunto, consultas ou base
    suite: str
    text: str


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def needs_months(suite: str) -> bool:
    return any(c.needs_months for c in SUITES[suite])


def write_baseline(
    runs: dict[str, Path], *, month: str, months: tuple[str, ...], out: Path, root: Path = Path(".")
) -> dict[str, object]:
    """Grava a base a partir dos JSON de execucoes reais, um por conjunto."""
    unknown = [s for s in runs if s not in MEASURED_SUITES]
    if unknown:
        raise EvaluationError(f"conjuntos desconhecidos: {unknown}; os validos sao {list(MEASURED_SUITES)}")
    if any(needs_months(s) for s in runs) and month not in months:
        raise EvaluationError("--months precisa incluir --month")
    entries: dict[str, dict[str, str]] = {}
    for suite, path in sorted(runs.items()):
        payload, results = load_run(path)
        if payload.get("model") is None:
            raise EvaluationError(f"{path} e uma execucao de referencia, nao do modelo")
        if not payload.get("prompt_sha"):
            raise EvaluationError(f"{path} nao registra o prompt (execucao antiga); rode de novo")
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise EvaluationError(f"{path} precisa estar dentro de {root}") from exc
        entries[suite] = {"file": relative.as_posix(), "sha256": sha256_of(path)}
        wrong = {r.id for r in results} ^ {c.id for c in SUITES[suite]}
        if wrong:
            raise EvaluationError(
                f"{path} nao tem as perguntas do conjunto {suite}: {sorted(wrong)[:MAX_LISTED]}"
            )
    payload_out: dict[str, object] = {
        "version": BASELINE_VERSION,
        "month": month,
        "months": list(months),
        "runs": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload_out


def load_baseline(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationError(f"nao consegui ler a base {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION or "runs" not in data:
        raise EvaluationError(f"{path} nao e uma base valida (versao {BASELINE_VERSION})")
    return data


# --------------------------------------------------------------------------- #
# Verificacoes
# --------------------------------------------------------------------------- #


def system_prompt(gold_dir: Path, *, month: str, months: tuple[str, ...] | None) -> str:
    columns = gold_columns(gold_dir, month, list(TABLES))
    return build_system_prompt(columns, month=month, months=months)


def reference_findings(
    gold_dir: Path, *, month: str, months: tuple[str, ...], suites: tuple[str, ...] = MEASURED_SUITES
) -> list[Finding]:
    findings: list[Finding] = []
    executor = DuckDBExecutor(gold_dir)
    try:
        for suite in suites:
            with_months = months if needs_months(suite) else None
            cases: tuple[EvalCase, ...] = SUITES[suite]
            system = system_prompt(gold_dir, month=month, months=with_months)
            try:
                results = evaluate(
                    cases,
                    month=month,
                    months=with_months,
                    system=system,
                    llm=ReferenceLLM(),
                    executor=executor,
                )
            except EvaluationError as exc:
                findings.append(Finding(ERROR, "referencias", suite, str(exc)))
                continue
            failed = [r for r in results if not r.passed]
            if failed:
                listed = "; ".join(f"{r.id} ({r.reason})" for r in failed[:MAX_LISTED])
                findings.append(
                    Finding(
                        ERROR, "referencias", suite, f"{len(failed)} de {len(results)} falharam: {listed}"
                    )
                )
            else:
                findings.append(
                    Finding(OK, "referencias", suite, f"{len(results)} de {len(results)} perguntas passam")
                )
    finally:
        executor.close()
    return findings


def baseline_findings(
    baseline: dict[str, object], *, gold_dir: Path, root: Path, month: str
) -> list[Finding]:
    findings: list[Finding] = []
    runs = baseline["runs"]
    assert isinstance(runs, dict)
    months = tuple(baseline.get("months") or (month,))  # type: ignore[arg-type]
    if baseline.get("month") != month:
        findings.append(
            Finding(ERROR, "base", "-", f"a base e do mes {baseline.get('month')} e o portao roda em {month}")
        )
        return findings

    for suite in MEASURED_SUITES:
        entry = runs.get(suite)
        if entry is None:
            findings.append(
                Finding(NOTE, "base", suite, "sem medicao com o modelo na base (nada a comparar)")
            )
            continue
        path = root / entry["file"]
        if not path.is_file():
            findings.append(Finding(ERROR, "base", suite, f"arquivo da base nao existe: {entry['file']}"))
            continue
        if sha256_of(path) != entry["sha256"]:
            findings.append(
                Finding(
                    ERROR,
                    "base",
                    suite,
                    f"{entry['file']} mudou desde que entrou na base; grave a base de novo se foi proposital",
                )
            )
            continue
        payload, results = load_run(path)
        with_months = months if needs_months(suite) else None

        expected_sha = prompt_version(system_prompt(gold_dir, month=month, months=with_months))
        if payload.get("prompt_sha") != expected_sha:
            findings.append(
                Finding(
                    ERROR,
                    "prompt",
                    suite,
                    f"o prompt mudou desde a medicao (medido {payload.get('prompt_sha')}, atual "
                    f"{expected_sha}); rode a avaliacao com o modelo e grave a base de novo",
                )
            )
        else:
            findings.append(Finding(OK, "prompt", suite, f"o prompt e o da medicao ({expected_sha})"))

        have, want = {r.id for r in results}, {c.id for c in SUITES[suite]}
        if have != want:
            diff = sorted(have ^ want)
            findings.append(
                Finding(
                    ERROR,
                    "conjunto",
                    suite,
                    f"as perguntas do conjunto mudaram desde a medicao ({len(diff)} diferentes: "
                    f"{', '.join(diff[:MAX_LISTED])})",
                )
            )
        else:
            findings.append(Finding(OK, "conjunto", suite, f"{len(want)} perguntas, as mesmas da medicao"))

        rejected = _guard_rejections(results, month=month, months=with_months)
        if rejected:
            listed = "; ".join(f"{case} ({why})" for case, why in rejected[:MAX_LISTED])
            findings.append(
                Finding(
                    ERROR,
                    "consultas",
                    suite,
                    f"{len(rejected)} consultas medidas nao passam na guarda de hoje: {listed}",
                )
            )
        else:
            sqls = {r.final_sql for r in results if r.final_sql}
            findings.append(
                Finding(OK, "consultas", suite, f"as {len(sqls)} consultas medidas passam na guarda de hoje")
            )
    return findings


def _guard_rejections(
    results: list[CaseResult], *, month: str, months: tuple[str, ...] | None
) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in results:
        if not r.final_sql or r.final_sql in seen:
            continue
        seen.add(r.final_sql)
        try:
            guard(r.final_sql, month=month, months=months)
        except SqlRejected as exc:
            out.append((r.id, str(exc).split(".")[0][:80]))
    return out


def check(*, gold_dir: Path, baseline_path: Path, root: Path, month: str = DEFAULT_MONTH) -> list[Finding]:
    baseline = load_baseline(baseline_path)
    months = tuple(baseline.get("months") or (month,))  # type: ignore[arg-type]
    findings = reference_findings(gold_dir, month=month, months=months)
    findings += baseline_findings(baseline, gold_dir=gold_dir, root=root, month=month)
    return findings


def render_findings(
    findings: list[Finding], baseline: dict[str, object] | None = None, root: Path = Path(".")
) -> str:
    marks = {OK: "ok", ERROR: "**ERRO**", NOTE: "aviso"}
    lines = ["# Portão da avaliação do agente (sem modelo, sem custo)", ""]
    failed = [f for f in findings if f.level == ERROR]
    lines += [
        "Falhou." if failed else "Passou.",
        "",
        "| Verificação | Conjunto | Resultado | Detalhe |",
        "|---|---|---|---|",
    ]
    lines += [f"| {f.check} | {f.suite} | {marks[f.level]} | {f.text} |" for f in findings]
    if baseline:
        lines += [
            "",
            "## Medições da base (execução real, não refeita aqui)",
            "",
            "| Conjunto | Acerto | Arquivo |",
            "|---|---|---|",
        ]
        runs = baseline["runs"]
        assert isinstance(runs, dict)
        for suite in MEASURED_SUITES:
            entry = runs.get(suite)
            if not entry:
                continue
            try:
                payload, results = load_run(root / entry["file"])
            except (OSError, KeyError, ValueError, EvaluationError):
                lines.append(f"| {suite} | ilegível | `{entry['file']}` |")
                continue
            s = summarize(results)
            accuracy = str(round(s.accuracy * 100, 1)).replace(".", ",")
            lines.append(
                f"| {suite} | {s.passed} de {s.cases} ({accuracy}%) "
                f"| `{entry['file']}` ({payload.get('model')}, {payload.get('generated_on')}) |"
            )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Comparacao com a base
# --------------------------------------------------------------------------- #


def compare_with_baseline(suite: str, new_path: Path, *, baseline_path: Path, root: Path) -> tuple[str, bool]:
    """Devolve (relatorio, regrediu). So conta como regressao uma queda maior do que o acaso."""
    baseline = load_baseline(baseline_path)
    runs = baseline["runs"]
    assert isinstance(runs, dict)
    entry = runs.get(suite)
    if entry is None:
        raise EvaluationError(f"a base nao tem uma medicao do conjunto {suite}")
    payload_a, results_a = load_run(root / entry["file"])
    payload_b, results_b = load_run(new_path)
    pair = paired(results_a, results_b)
    regressed = pair.significant and pair.diff < 0
    report = compare(payload_a, results_a, payload_b, results_b)
    notes = []
    if payload_a.get("prompt_sha") != payload_b.get("prompt_sha"):
        notes.append(
            f"Os prompts são diferentes (base {payload_a.get('prompt_sha')}, "
            f"nova {payload_b.get('prompt_sha')}): "
            "a comparação mede a mudança do prompt."
        )
    verdict = (
        "**Regressão:** a queda de acerto é maior do que o acaso explicaria."
        if regressed
        else "Sem regressão distinguível do acaso."
    )
    return report + "\n".join(["", *notes, "", verdict, ""]), regressed


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def _parse_runs(items: list[str]) -> dict[str, Path]:
    runs: dict[str, Path] = {}
    for item in items:
        suite, sep, path = item.partition("=")
        if not sep or not path:
            raise EvaluationError(f"use --run conjunto=caminho (recebi {item!r})")
        runs[suite] = Path(path)
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portao da avaliacao do agente (sem modelo, sem custo).")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="verificacoes deterministicas; sai com 1 se alguma falhar")
    chk.add_argument("--gold-dir", type=Path, required=True)
    chk.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    chk.add_argument("--root", type=Path, default=Path("."), help="raiz do repositorio")
    chk.add_argument("--month", default=DEFAULT_MONTH)

    base = sub.add_parser("baseline", help="grava a base a partir de execucoes reais")
    base.add_argument("--run", action="append", default=[], metavar="CONJUNTO=JSON", required=True)
    base.add_argument("--month", default=DEFAULT_MONTH)
    base.add_argument("--months", required=True, help="meses do conjunto 'meses', separados por virgula")
    base.add_argument("--out", type=Path, default=DEFAULT_BASELINE)
    base.add_argument("--root", type=Path, default=Path("."))

    cmp_ = sub.add_parser("compare", help="compara uma execucao nova com a base; sai com 1 se regrediu")
    cmp_.add_argument("new_run", type=Path)
    cmp_.add_argument("--suite", required=True, choices=MEASURED_SUITES)
    cmp_.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    cmp_.add_argument("--root", type=Path, default=Path("."))
    cmp_.add_argument("--out", type=Path, help="grava o relatorio em Markdown")

    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            months = tuple(sorted({m.strip() for m in args.months.split(",") if m.strip()}))
            write_baseline(
                _parse_runs(args.run), month=args.month, months=months, out=args.out, root=args.root
            )
            print(f"base gravada em {args.out}")
            return 0
        if args.command == "compare":
            report, regressed = compare_with_baseline(
                args.suite, args.new_run, baseline_path=args.baseline, root=args.root
            )
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(report, encoding="utf-8")
            print(report)
            return 1 if regressed else 0
        findings = check(
            gold_dir=args.gold_dir, baseline_path=args.baseline, root=args.root, month=args.month
        )
        report = render_findings(findings, load_baseline(args.baseline), args.root)
    except (OSError, KeyError, ValueError, EvaluationError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return 1 if any(f.level == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
