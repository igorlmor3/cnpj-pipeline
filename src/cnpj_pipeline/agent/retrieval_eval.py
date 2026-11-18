"""Avaliacao da recuperacao: a busca traz, entre os primeiros resultados, um trecho que responde?

    python -m cnpj_pipeline.agent.retrieval_eval --root . --suite dev
    python -m cnpj_pipeline.agent.retrieval_eval --root . --suite reservado --audience all

Mede a busca sozinha, sem modelo e sem custo, e separada da resposta: se o agente erra, da para saber
se foi a busca que nao trouxe o trecho ou o modelo que nao o usou. Um trecho e relevante se contem toda
a evidencia do caso (ver `retrieval_cases`). Metricas: acerto nos k primeiros (hit@1, @3, @5) e MRR@5
(media do inverso da posicao do primeiro trecho relevante), com intervalo de 95% de Wilson.

Com `--audience public` (padrao, o que um visitante enxerga), um caso so conta se algum trecho
publico o responde; os demais sao listados como sem fonte publica. Em qualquer modo o relatorio
conta os resultados que vieram de um publico nao permitido: o numero precisa ser zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cnpj_pipeline.agent.eval_stats import wilson_interval
from cnpj_pipeline.agent.retrieval import (
    AUDIENCES,
    PUBLIC,
    BM25Index,
    Chunk,
    Retriever,
    Source,
    build_index,
    fold,
)
from cnpj_pipeline.agent.retrieval_cases import SUITES, RetrievalCase

KS = (1, 3, 5)
MAX_K = max(KS)


@dataclass(frozen=True)
class Outcome:
    id: str
    category: str
    question: str
    answerable: bool  # algum trecho visivel contem a evidencia
    relevant_chunks: int
    rank: int | None  # posicao do primeiro trecho relevante entre os MAX_K primeiros
    top: list[str]  # citacoes dos 3 primeiros resultados
    leaked: int  # resultados de um publico nao permitido (deve ser 0)


def relevant_ids(case: RetrievalCase, chunks: list[Chunk]) -> set[str]:
    evidence = [fold(e) for e in case.evidence]
    found = set()
    for chunk in chunks:
        text = fold(f"{chunk.heading}\n{chunk.text}")
        if all(e in text for e in evidence):
            found.add(chunk.id)
    return found


def evaluate_retrieval(
    cases: tuple[RetrievalCase, ...],
    retriever: Retriever,
    chunks: list[Chunk],
    *,
    audiences: frozenset[str],
) -> list[Outcome]:
    visible = [c for c in chunks if c.audience in audiences]
    outcomes = []
    for case in cases:
        relevant = relevant_ids(case, visible)
        hits = retriever.search(case.question, audiences=audiences, k=MAX_K)
        rank = next((h.rank for h in hits if h.chunk.id in relevant), None)
        outcomes.append(
            Outcome(
                id=case.id,
                category=case.category,
                question=case.question,
                answerable=bool(relevant),
                relevant_chunks=len(relevant),
                rank=rank,
                top=[h.chunk.citation for h in hits[:3]],
                leaked=sum(h.chunk.audience not in audiences for h in hits),
            )
        )
    return outcomes


@dataclass(frozen=True)
class RetrievalSummary:
    cases: int
    answerable: int
    unanswerable_ids: list[str]
    hit_at: dict[int, int]
    hit_rate: dict[int, float]
    hit_ci: dict[int, tuple[float, float]]
    mrr: float
    leaked: int
    by_category: dict[str, dict[str, int]]


def summarize_retrieval(outcomes: list[Outcome]) -> RetrievalSummary:
    scored = [o for o in outcomes if o.answerable]
    n = len(scored)
    hit_at = {k: sum(o.rank is not None and o.rank <= k for o in scored) for k in KS}
    by_category: dict[str, dict[str, int]] = {}
    for o in scored:
        bucket = by_category.setdefault(o.category, {"cases": 0, "hit_at_3": 0})
        bucket["cases"] += 1
        bucket["hit_at_3"] += int(o.rank is not None and o.rank <= 3)
    return RetrievalSummary(
        cases=len(outcomes),
        answerable=n,
        unanswerable_ids=[o.id for o in outcomes if not o.answerable],
        hit_at=hit_at,
        hit_rate={k: (hit_at[k] / n if n else 0.0) for k in KS},
        hit_ci={k: wilson_interval(hit_at[k], n) for k in KS},
        mrr=(sum(1 / o.rank for o in scored if o.rank) / n) if n else 0.0,
        leaked=sum(o.leaked for o in outcomes),
        by_category=by_category,
    )


def _pct(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def corpus_fingerprint(sources: list[Source]) -> str:
    digest = hashlib.sha256()
    for s in sources:
        digest.update(f"{s.path}\0{s.audience}\0{s.sha256}\n".encode())
    return digest.hexdigest()[:12]


def render_report(
    outcomes: list[Outcome],
    *,
    suite: str,
    audience: str,
    generated_on: str,
    fingerprint: str,
    chunks: int,
) -> str:
    s = summarize_retrieval(outcomes)
    out = [
        f"# Avaliação da recuperação: conjunto {suite}, público {audience}",
        "",
        f"Gerado em {generated_on}. Busca BM25 sobre a documentação do repositório "
        f"({chunks} trechos; versão do corpus `{fingerprint}`). Sem modelo e sem custo.",
        "",
        "## Resumo",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Perguntas com fonte neste público | {s.answerable} de {s.cases} |",
    ]
    for k in KS:
        lo, hi = s.hit_ci[k]
        out.append(
            f"| Acerto nos {k} primeiros | {s.hit_at[k]} de {s.answerable} ({_pct(s.hit_rate[k])}; "
            f"intervalo de 95%: {_pct(lo)} a {_pct(hi)}) |"
        )
    out += [
        f"| MRR@{MAX_K} | {s.mrr:.3f}".replace(".", ",") + " |",
        f"| Resultados de público não permitido | {s.leaked} |",
        "",
        "## Por categoria (acerto nos 3 primeiros)",
        "",
        "| Categoria | Acertos | Perguntas |",
        "|---|---|---|",
    ]
    out += [f"| {cat} | {b['hit_at_3']} | {b['cases']} |" for cat, b in sorted(s.by_category.items())]
    misses = [o for o in outcomes if o.answerable and (o.rank is None or o.rank > 3)]
    if misses:
        out += ["", "## Perguntas sem trecho relevante nos 3 primeiros", ""]
        for o in misses:
            where = f"posição {o.rank}" if o.rank else f"fora dos {MAX_K} primeiros"
            out += [f"**{o.id}** ({where}): {o.question}", ""]
            out += [f"- {c}" for c in o.top] + [""]
    if s.unanswerable_ids:
        out += [
            "## Sem fonte neste público",
            "",
            "A evidência dessas perguntas está só em documentos que este público não vê: "
            + ", ".join(s.unanswerable_ids)
            + ".",
            "",
        ]
    out += [
        "## Como ler",
        "",
        "- Um trecho é relevante se contém toda a evidência do caso, sem diferença de caixa nem de "
        "acento. Isso mede se a busca traz o trecho que responde, não se o modelo o usa bem.",
        "- Com poucas perguntas o intervalo é largo; compare duas buscas pelas mesmas perguntas, não "
        "por intervalos que se sobrepõem.",
        "- O conjunto `reservado` não é usado para ajustar a busca; o `dev` é.",
        "",
    ]
    return "\n".join(out)


def results_json(
    outcomes: list[Outcome], *, suite: str, audience: str, generated_on: str, fingerprint: str, chunks: int
) -> str:
    s = summarize_retrieval(outcomes)
    payload = {
        "suite": suite,
        "audience": audience,
        "generated_on": generated_on,
        "corpus": fingerprint,
        "chunks": chunks,
        "summary": {
            **{k: v for k, v in asdict(s).items() if k not in {"hit_at", "hit_rate", "hit_ci"}},
            "hit_at": {str(k): v for k, v in s.hit_at.items()},
            "hit_rate": {str(k): v for k, v in s.hit_rate.items()},
            "hit_ci": {str(k): list(v) for k, v in s.hit_ci.items()},
        },
        "cases": [asdict(o) for o in outcomes],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run(
    root: Path, *, suite: str, audience: str, out_dir: Path | None, index: BM25Index | None = None
) -> tuple[list[Outcome], str]:
    built, sources = build_index(root)
    index = index or built
    audiences = AUDIENCES if audience == "all" else frozenset({PUBLIC})
    outcomes = evaluate_retrieval(SUITES[suite], index, list(index.chunks), audiences=audiences)
    fields = {
        "suite": suite,
        "audience": audience,
        "generated_on": datetime.now(UTC).date().isoformat(),
        "fingerprint": corpus_fingerprint(sources),
        "chunks": len(index.chunks),
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{suite}_{audience}"
        (out_dir / f"{stem}.md").write_text(render_report(outcomes, **fields), encoding="utf-8")
        (out_dir / f"{stem}.json").write_text(results_json(outcomes, **fields), encoding="utf-8")
    return outcomes, fields["fingerprint"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avalia a recuperacao de trechos da documentacao.")
    parser.add_argument("--root", type=Path, default=Path("."), help="raiz do repositorio")
    parser.add_argument("--suite", choices=sorted(SUITES), default="dev")
    parser.add_argument("--audience", choices=["public", "all"], default="public")
    parser.add_argument("--out-dir", type=Path, default=Path("docs/retrieval_eval"))
    args = parser.parse_args(argv)

    outcomes, _ = run(args.root, suite=args.suite, audience=args.audience, out_dir=args.out_dir)
    s = summarize_retrieval(outcomes)
    print(
        f"acerto nos 1/3/5 primeiros: {s.hit_at[1]}/{s.hit_at[3]}/{s.hit_at[5]} de {s.answerable} "
        f"({_pct(s.hit_rate[1])}/{_pct(s.hit_rate[3])}/{_pct(s.hit_rate[5])}); MRR@{MAX_K} "
        f"{s.mrr:.3f}; resultados de público não permitido: {s.leaked}"
    )
    print(f"relatório em {args.out_dir / f'{args.suite}_{args.audience}.md'}")
    return 0 if s.leaked == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
