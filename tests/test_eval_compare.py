from __future__ import annotations

import json

import pytest

from cnpj_pipeline.agent.eval_compare import compare, load_run, main
from cnpj_pipeline.agent.evaluation import CaseResult, EvaluationError, results_json, summarize


def case(id, passed=True, run=1, cost=0.001, elapsed=1.0):
    return CaseResult(
        id=id, category="x", kind="data", question=f"pergunta {id}?", passed=passed,
        reason="" if passed else "resultado diferente do esperado", status="answered", llm_calls=2,
        queries=1, failed_queries=0, first_query_ok=True, input_tokens=100, output_tokens=10,
        cost_usd=cost, elapsed_s=elapsed, run=run,
    )  # fmt: skip


def write(path, results, *, model="m", sha="abc123"):
    path.write_text(
        results_json(
            results, month="2026-10", label=model, generated_on="2026-11-18", model=model, prompt_sha=sha
        ),
        encoding="utf-8",
    )
    return path


def test_summarize_has_intervals_costs_and_tokens():
    results = [case("a"), case("b"), case("c", passed=False, cost=0.003, elapsed=4.0)]
    s = summarize(results)

    assert s.ci_method == "wilson" and s.questions == 3
    assert 0.0 < s.accuracy_ci_low < s.accuracy < s.accuracy_ci_high < 1.0
    assert s.cost_per_correct_usd == pytest.approx(0.005 / 2)
    assert (s.mean_input_tokens, s.mean_output_tokens) == (100, 10)
    assert s.cost_p95_usd == pytest.approx(0.003)


def test_with_repeats_the_interval_resamples_questions_not_runs():
    results = [case(q, passed=(q != "d" or r == 1), run=r) for q in "abcd" for r in (1, 2, 3)]
    s = summarize(results)

    assert s.ci_method == "reamostragem das perguntas" and s.questions == 4 and s.cases == 12
    # 4 perguntas bastam para um intervalo largo, mesmo com 12 execucoes
    assert s.accuracy_ci_high - s.accuracy_ci_low > 0.15 or s.accuracy_ci_high == 1.0


def test_cost_per_correct_is_none_when_nothing_passed():
    assert summarize([case("a", passed=False)]).cost_per_correct_usd is None


def test_compare_single_runs_uses_mcnemar_and_lists_changed_questions(tmp_path):
    ids = [f"q{i}" for i in range(12)]
    a = [case(i, passed=(i not in {"q0", "q1", "q2", "q3", "q4", "q5"})) for i in ids]  # A erra q0..q5
    b = [case(i, passed=True, cost=0.002) for i in ids]
    text = compare(
        *load_run(write(tmp_path / "a.json", a)), *load_run(write(tmp_path / "b.json", b, model="n"))
    )

    assert "12 perguntas em comum." in text
    assert "McNemar" in text and "p = 0.031" in text  # 6 discordantes, todos a favor de B
    assert "maior do que o acaso" in text
    assert "- q0: A 0% e B 100%" in text
    assert "m, prompt abc123" in text and "n, prompt abc123" in text


def test_compare_with_one_discordant_question_is_not_significant(tmp_path):
    a = [case("a"), case("b"), case("c", passed=False)]
    b = [case("a"), case("b"), case("c")]
    text = compare(*load_run(write(tmp_path / "a.json", a)), *load_run(write(tmp_path / "b.json", b)))

    assert "p = 1.000" in text and "não é distinguível do acaso" in text


def test_compare_pairs_only_the_common_questions_and_says_so(tmp_path):
    a = [case("a"), case("b"), case("so_a")]
    b = [case("a"), case("b"), case("so_b"), case("so_b2")]
    text = compare(*load_run(write(tmp_path / "a.json", a)), *load_run(write(tmp_path / "b.json", b)))

    assert "2 perguntas em comum (A tem 1 a mais, B tem 2 a mais)." in text


def test_compare_with_repeats_reports_the_difference_interval(tmp_path):
    a = [case(q, passed=(r == 1), run=r) for q in "abcdef" for r in (1, 2, 3)]
    b = [case(q, passed=True, run=r) for q in "abcdef" for r in (1, 2, 3)]
    text = compare(*load_run(write(tmp_path / "a.json", a)), *load_run(write(tmp_path / "b.json", b)))

    assert "Diferença média da taxa de acerto: +66.7 pontos percentuais" in text
    assert "McNemar" not in text


def test_compare_rejects_runs_without_common_questions(tmp_path):
    a = load_run(write(tmp_path / "a.json", [case("a")]))
    b = load_run(write(tmp_path / "b.json", [case("b")]))
    with pytest.raises(EvaluationError):
        compare(*a, *b)


def test_cli_prints_or_writes_the_report_and_fails_cleanly(tmp_path, capsys):
    pa = write(tmp_path / "a.json", [case("a"), case("b", passed=False)])
    pb = write(tmp_path / "b.json", [case("a"), case("b")])

    assert main([str(pa), str(pb)]) == 0
    assert "# Comparação de duas execuções" in capsys.readouterr().out

    out = tmp_path / "sub" / "c.md"
    assert main([str(pa), str(pb), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Comparação")

    assert main([str(pa), str(tmp_path / "nao_existe.json")]) == 2
    assert "erro" in capsys.readouterr().err

    (tmp_path / "vazio.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert main([str(pa), str(tmp_path / "vazio.json")]) == 2
