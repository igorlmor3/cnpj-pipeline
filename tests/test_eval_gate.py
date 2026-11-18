from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from cnpj_pipeline import demo
from cnpj_pipeline.agent import eval_gate
from cnpj_pipeline.agent.eval_gate import ERROR, NOTE, OK, Finding
from cnpj_pipeline.agent.evaluation import CaseResult, EvaluationError, prompt_version, results_json
from cnpj_pipeline.agent.evaluation_cases import SUITES

MONTH = "2026-10"
MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10")


@pytest.fixture(scope="session")
def gold(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("demo")
    assert demo.run_demo(base, month=MONTH, rows=600) == 0
    return base / "gold"


def result(case, *, passed=True, sql="SELECT 1", run=1):
    return CaseResult(
        id=case.id, category=case.category, kind=case.kind, question=case.question, passed=passed,
        reason="" if passed else "resultado diferente do esperado", status="answered", llm_calls=2,
        queries=1, failed_queries=0, first_query_ok=True, input_tokens=100, output_tokens=10,
        cost_usd=0.001, elapsed_s=1.0, final_sql=sql, run=run,
    )  # fmt: skip


def measured_sha(gold: Path) -> str:
    return prompt_version(eval_gate.system_prompt(gold, month=MONTH, months=MONTHS))


def write_run(path: Path, results, *, sha: str, model: str | None = "modelo-x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        results_json(results, month=MONTH, label="x", generated_on="2026-11-18", model=model, prompt_sha=sha),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def repo(tmp_path, gold) -> Path:
    """Repositorio de mentira com uma medicao do conjunto `meses` e a base apontando para ela."""
    cases = SUITES["meses"]
    sql = "SELECT SUM(empresas_ativas) FROM gold_mart_simples_mei_uf WHERE uf = 'SP'"
    write_run(
        tmp_path / "docs/agent_eval/meses.json",
        [result(c, sql=sql) for c in cases],
        sha=measured_sha(gold),
    )
    eval_gate.write_baseline(
        {"meses": tmp_path / "docs/agent_eval/meses.json"},
        month=MONTH, months=MONTHS, out=tmp_path / "docs/agent_eval/baseline.json", root=tmp_path,
    )  # fmt: skip
    return tmp_path


def run_check(repo: Path, gold: Path) -> list[Finding]:
    return eval_gate.baseline_findings(
        eval_gate.load_baseline(repo / "docs/agent_eval/baseline.json"), gold_dir=gold, root=repo, month=MONTH
    )


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == ERROR]


def test_every_reference_passes_the_guard_and_the_judge_on_the_demo_gold(gold):
    findings = eval_gate.reference_findings(gold, month=MONTH, months=MONTHS)

    assert {f.suite for f in findings} == set(eval_gate.MEASURED_SUITES)
    assert [f for f in findings if f.level != OK] == []


def test_a_broken_reference_fails_the_gate(gold, monkeypatch):
    broken = replace(SUITES["meses"][0], reference_sql="SELECT 1 FROM tabela_que_nao_existe")
    monkeypatch.setitem(SUITES, "meses", (broken, *SUITES["meses"][1:]))

    findings = eval_gate.reference_findings(gold, month=MONTH, months=MONTHS, suites=("meses",))

    assert [f.level for f in findings] == [ERROR] and findings[0].check == "referencias"


def test_a_reference_the_guard_now_rejects_fails_the_gate(gold, monkeypatch):
    rejected = replace(SUITES["meses"][0], reference_sql="DELETE FROM gold_mart_simples_mei_uf")
    monkeypatch.setitem(SUITES, "meses", (rejected, *SUITES["meses"][1:]))

    findings = eval_gate.reference_findings(gold, month=MONTH, months=MONTHS, suites=("meses",))

    assert [f.level for f in findings] == [ERROR]


def test_an_intact_baseline_passes_and_unmeasured_suites_are_notes(repo, gold):
    findings = run_check(repo, gold)

    assert errors(findings) == []
    assert {f.suite for f in findings if f.level == NOTE} == set(eval_gate.MEASURED_SUITES) - {"meses"}
    assert {f.check for f in findings if f.suite == "meses"} == {"prompt", "conjunto", "consultas"}


def test_a_run_file_changed_after_entering_the_baseline_is_caught(repo, gold):
    path = repo / "docs/agent_eval/meses.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    (found,) = errors(run_check(repo, gold))

    assert found.check == "base" and "mudou" in found.text


def test_a_missing_run_file_is_caught(repo, gold):
    (repo / "docs/agent_eval/meses.json").unlink()

    (found,) = errors(run_check(repo, gold))

    assert found.check == "base" and "nao existe" in found.text


def test_a_prompt_that_changed_since_the_measurement_is_caught(repo, gold, monkeypatch):
    real = eval_gate.build_system_prompt
    monkeypatch.setattr(eval_gate, "build_system_prompt", lambda *a, **k: real(*a, **k) + "\nRegra nova.")

    (found,) = errors(run_check(repo, gold))

    assert found.check == "prompt" and "mudou" in found.text


def test_a_changed_question_set_is_caught(repo, gold, monkeypatch):
    monkeypatch.setitem(SUITES, "meses", SUITES["meses"][:-1])

    found = errors(run_check(repo, gold))

    assert [f.check for f in found] == ["conjunto"]


def test_a_measured_query_the_current_guard_rejects_is_caught(tmp_path, gold):
    cases = SUITES["meses"]
    bad = "SELECT * FROM silver_estabelecimentos"
    write_run(
        tmp_path / "docs/agent_eval/meses.json",
        [result(cases[0], sql=bad), *map(result, cases[1:])],
        sha=measured_sha(gold),
    )
    eval_gate.write_baseline(
        {"meses": tmp_path / "docs/agent_eval/meses.json"},
        month=MONTH, months=MONTHS, out=tmp_path / "docs/agent_eval/baseline.json", root=tmp_path,
    )  # fmt: skip

    found = errors(run_check(tmp_path, gold))

    assert [f.check for f in found] == ["consultas"] and cases[0].id in found[0].text


def test_a_baseline_of_another_month_is_refused(repo, gold):
    baseline = eval_gate.load_baseline(repo / "docs/agent_eval/baseline.json")

    (found,) = errors(eval_gate.baseline_findings(baseline, gold_dir=gold, root=repo, month="2026-11"))

    assert found.check == "base"


def test_the_baseline_refuses_reference_runs_old_runs_and_foreign_files(tmp_path, gold):
    cases = SUITES["meses"]
    reference = write_run(tmp_path / "ref.json", [result(c) for c in cases], sha="abc", model=None)
    no_sha = write_run(tmp_path / "old.json", [result(c) for c in cases], sha="")
    ok = write_run(tmp_path / "ok.json", [result(c) for c in cases], sha="abc")
    outside = tmp_path / "fora" / "ok.json"
    outside.parent.mkdir()
    shutil.copy(ok, outside)
    partial = write_run(tmp_path / "partial.json", [result(c) for c in cases[:2]], sha="abc")
    out = tmp_path / "baseline.json"

    def attempt(suite, path, root=tmp_path):
        eval_gate.write_baseline({suite: path}, month=MONTH, months=MONTHS, out=out, root=root)

    with pytest.raises(EvaluationError, match="referencia"):
        attempt("meses", reference)
    with pytest.raises(EvaluationError, match="antiga"):
        attempt("meses", no_sha)
    with pytest.raises(EvaluationError, match="dentro de"):
        attempt("meses", outside, root=tmp_path / "docs")
    with pytest.raises(EvaluationError, match="nao tem as perguntas"):
        attempt("meses", partial)
    with pytest.raises(EvaluationError, match="desconhecidos"):
        attempt("inventado", ok)
    with pytest.raises(EvaluationError, match="months"):
        eval_gate.write_baseline({"meses": ok}, month=MONTH, months=("2026-09",), out=out, root=tmp_path)


def test_a_file_that_is_not_a_baseline_is_refused(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")

    with pytest.raises(EvaluationError, match="base valida"):
        eval_gate.load_baseline(path)
    with pytest.raises(EvaluationError, match="nao consegui ler"):
        eval_gate.load_baseline(tmp_path / "nao_existe.json")


def test_compare_fails_only_on_a_drop_larger_than_chance(repo, gold):
    cases = SUITES["meses"]
    baseline = repo / "docs/agent_eval/baseline.json"
    sha = measured_sha(gold)
    same = write_run(repo / "novo_igual.json", [result(c) for c in cases], sha=sha)
    one_less = write_run(
        repo / "novo_um.json", [result(cases[0], passed=False), *map(result, cases[1:])], sha=sha
    )

    report, regressed = eval_gate.compare_with_baseline("meses", same, baseline_path=baseline, root=repo)
    assert not regressed and "Sem regressão" in report
    _, regressed = eval_gate.compare_with_baseline("meses", one_less, baseline_path=baseline, root=repo)
    assert not regressed  # uma pergunta de seis nao se distingue do acaso


def test_compare_flags_a_large_drop_and_a_changed_prompt(tmp_path, gold):
    cases = tuple(SUITES["dev"])
    sha = measured_sha(gold)
    good = write_run(tmp_path / "docs/a.json", [result(c) for c in cases], sha=sha)
    eval_gate.write_baseline(
        {"dev": good}, month=MONTH, months=MONTHS, out=tmp_path / "b.json", root=tmp_path
    )
    half = [result(c, passed=i % 2 == 0) for i, c in enumerate(cases)]
    worse = write_run(tmp_path / "worse.json", half, sha="outro")

    report, regressed = eval_gate.compare_with_baseline(
        "dev", worse, baseline_path=tmp_path / "b.json", root=tmp_path
    )

    assert regressed and "Regressão" in report and "prompts são diferentes" in report
    with pytest.raises(EvaluationError, match="nao tem uma medicao"):
        eval_gate.compare_with_baseline("holdout", worse, baseline_path=tmp_path / "b.json", root=tmp_path)


def test_the_command_line_exit_codes(repo, gold, capsys, monkeypatch):
    summary = repo / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    args = ["check", "--gold-dir", str(gold), "--root", str(repo), "--month", MONTH,
            "--baseline", str(repo / "docs/agent_eval/baseline.json")]  # fmt: skip

    assert eval_gate.main(args) == 0
    assert "Passou." in capsys.readouterr().out and "Passou." in summary.read_text(encoding="utf-8")

    (repo / "docs/agent_eval/meses.json").write_text("{}", encoding="utf-8")
    assert eval_gate.main(args) == 1
    assert eval_gate.main(["check", "--gold-dir", str(gold), "--baseline", str(repo / "nada.json")]) == 2
