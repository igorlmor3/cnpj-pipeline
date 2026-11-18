from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cnpj_pipeline.agent import retrieval_eval
from cnpj_pipeline.agent.retrieval import INTERNAL, PUBLIC, BM25Index, Chunk, build_index
from cnpj_pipeline.agent.retrieval_cases import DEV_CASES, RESERVADO_CASES, SUITES, RetrievalCase
from cnpj_pipeline.agent.retrieval_eval import (
    Outcome,
    evaluate_retrieval,
    relevant_ids,
    render_report,
    results_json,
    summarize_retrieval,
)

REPO = Path(os.environ.get("CNPJ_CORPUS_ROOT", Path(__file__).resolve().parents[1]))
needs_docs = pytest.mark.skipif(
    not (REPO / "docs" / "agent_design.md").exists(), reason="documentacao do repositorio ausente"
)


def chunks() -> list[Chunk]:
    return [
        Chunk("a#1", "a.md", "Motor", "DuckDB lê CSV maior que a memória.", PUBLIC, 1),
        Chunk("a#2", "a.md", "Gold", "A gold tem marts agregados.", PUBLIC, 5),
        Chunk("b#1", "b.md", "Chaves", "As chaves do publicador ficam fora do Terraform.", INTERNAL, 1),
    ]


def case(cid, question, *evidence, category="regra"):
    return RetrievalCase(cid, category, question, tuple(evidence))


def test_a_chunk_is_relevant_only_if_it_has_all_the_evidence_ignoring_case_and_accents():
    c = case("x", "?", "DUCKDB", "memoria")
    assert relevant_ids(c, chunks()) == {"a#1"}
    assert relevant_ids(case("y", "?", "duckdb", "terraform"), chunks()) == set()
    assert relevant_ids(case("z", "?", "motor"), chunks()) == {"a#1"}  # o titulo conta


def test_rank_is_the_position_of_the_first_relevant_chunk():
    index = BM25Index(chunks())
    (hit,) = evaluate_retrieval(
        (case("c", "DuckDB lê CSV?", "duckdb", "csv"),), index, chunks(), audiences=frozenset({PUBLIC})
    )

    assert hit.rank == 1 and hit.answerable and hit.leaked == 0 and hit.top == ["a.md, seção Motor"]


def test_a_case_whose_evidence_is_only_internal_is_unanswerable_in_public_mode():
    index = BM25Index(chunks())
    c = case("i", "onde ficam as chaves do publicador?", "chaves do publicador")

    (public,) = evaluate_retrieval((c,), index, chunks(), audiences=frozenset({PUBLIC}))
    (everything,) = evaluate_retrieval((c,), index, chunks(), audiences=frozenset({PUBLIC, INTERNAL}))

    assert not public.answerable and public.rank is None and public.leaked == 0
    assert everything.answerable and everything.rank == 1


def test_a_miss_has_no_rank():
    index = BM25Index(chunks())
    (o,) = evaluate_retrieval(
        (case("m", "marts agregados?", "duckdb"),), index, chunks(), audiences=frozenset({PUBLIC})
    )
    assert o.answerable and o.rank is None


def outcome(cid, rank, *, answerable=True, category="regra", leaked=0):
    return Outcome(cid, category, "q", answerable, 1 if answerable else 0, rank, [], leaked)


def test_summary_counts_hits_mrr_and_skips_unanswerable_cases():
    outcomes = [
        outcome("a", 1),
        outcome("b", 2, category="numero"),
        outcome("c", 4),
        outcome("d", None),
        outcome("e", None, answerable=False),
    ]
    s = summarize_retrieval(outcomes)

    assert (s.cases, s.answerable, s.unanswerable_ids) == (5, 4, ["e"])
    assert s.hit_at == {1: 1, 3: 2, 5: 3}
    assert s.mrr == pytest.approx((1 + 1 / 2 + 1 / 4 + 0) / 4)
    assert s.by_category == {"regra": {"cases": 3, "hit_at_3": 1}, "numero": {"cases": 1, "hit_at_3": 1}}
    lo, hi = s.hit_ci[3]
    assert 0 < lo < 0.5 < hi < 1


def test_summary_of_nothing_does_not_divide_by_zero():
    s = summarize_retrieval([outcome("e", None, answerable=False)])
    assert s.answerable == 0 and s.mrr == 0.0 and s.hit_rate == {1: 0.0, 3: 0.0, 5: 0.0}


def test_leaks_are_added_up_and_shown_in_the_report():
    s = summarize_retrieval([outcome("a", 1, leaked=2), outcome("b", 1)])
    assert s.leaked == 2


def test_the_report_lists_misses_and_unanswerable_cases_and_uses_comma_decimals():
    outcomes = [outcome("a", 1), outcome("b", None), outcome("c", None, answerable=False)]
    text = render_report(
        outcomes, suite="dev", audience="public", generated_on="2026-11-18", fingerprint="abc123", chunks=10
    )

    assert "conjunto dev, público public" in text and "`abc123`" in text and "10 trechos" in text
    assert "**b** (fora dos 5 primeiros)" in text
    assert "só em documentos que este público não vê: c" in text
    assert "50,0%" in text and "0,500" in text


def test_the_json_report_is_valid_and_carries_every_case():
    outcomes = [outcome("a", 1), outcome("b", None)]
    data = json.loads(
        results_json(
            outcomes, suite="dev", audience="all", generated_on="2026-11-18", fingerprint="f", chunks=3
        )
    )

    assert data["summary"]["hit_at"] == {"1": 1, "3": 1, "5": 1}
    assert [c["id"] for c in data["cases"]] == ["a", "b"] and data["summary"]["leaked"] == 0


# --------------------------------------------------------------------------- #
# Os casos
# --------------------------------------------------------------------------- #


def test_case_ids_and_questions_are_unique_across_suites():
    cases = [*DEV_CASES, *RESERVADO_CASES]
    assert len({c.id for c in cases}) == len(cases)
    assert len({c.question.lower() for c in cases}) == len(cases)
    assert all(c.id.startswith("d_") for c in DEV_CASES) and all(
        c.id.startswith("r_") for c in RESERVADO_CASES
    )
    assert set(SUITES) == {"dev", "reservado", "all"} and len(SUITES["all"]) == len(cases)
    assert all(c.evidence and all(e.strip() for e in c.evidence) for c in cases)


@needs_docs
def test_every_case_is_answerable_from_the_repository_docs():
    built, _ = build_index(REPO)
    for c in SUITES["all"]:
        assert relevant_ids(c, list(built.chunks)), f"{c.id}: nenhum trecho contem a evidencia"


@needs_docs
def test_the_real_index_never_returns_internal_chunks_to_the_public():
    built, _ = build_index(REPO)
    outcomes = evaluate_retrieval(SUITES["all"], built, list(built.chunks), audiences=frozenset({PUBLIC}))
    assert sum(o.leaked for o in outcomes) == 0
    assert {c.audience for c in built.chunks} == {PUBLIC, INTERNAL}


@needs_docs
def test_the_baseline_does_not_regress():
    """Piso, nao meta: se a busca ou o chunking pioram, o teste avisa. Os valores medidos hoje estao
    nos relatorios de docs/retrieval_eval/."""
    built, _ = build_index(REPO)
    outcomes = evaluate_retrieval(SUITES["dev"], built, list(built.chunks), audiences=frozenset({PUBLIC}))
    s = summarize_retrieval(outcomes)
    assert s.hit_rate[5] >= 0.75 and s.mrr >= 0.5


@needs_docs
def test_cli_writes_both_reports_and_returns_zero_without_leaks(tmp_path, capsys):
    code = retrieval_eval.main(
        ["--root", str(REPO), "--suite", "dev", "--audience", "public", "--out-dir", str(tmp_path)]
    )

    assert code == 0
    assert (tmp_path / "dev_public.md").exists() and (tmp_path / "dev_public.json").exists()
    assert "acerto nos 1/3/5 primeiros" in capsys.readouterr().out
