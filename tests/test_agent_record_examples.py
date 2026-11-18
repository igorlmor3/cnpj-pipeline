"""Testes do gravador de exemplos do agente: so grava o que saiu como esperado, e de forma deterministica."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import MONTH
from test_agent_ask import GOOD_SQL, FakeLLM, turn
from test_gold import build_silver, run_gold

from cnpj_pipeline.agent import record_examples as rec
from cnpj_pipeline.agent.ask import Answer, QueryRecord, build_system_prompt
from cnpj_pipeline.agent.budget import Budget
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.executor import DuckDBExecutor
from cnpj_pipeline.cloud.athena import gold_columns


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("rec")
    build_silver(base)
    assert run_gold(base) == 0
    return base / "gold"


def good_script() -> list:
    """Quatro perguntas de dados (consulta + resposta) e uma recusa."""
    script: list = []
    for i in range(4):
        script += [turn("", GOOD_SQL), turn(f"Resposta {i}.")]
    script.append(turn("Não posso informar dados de um CNPJ específico."))
    return script


def run_record(gold_root: Path, script: list, months: tuple[str, ...] | None = None):
    executor = DuckDBExecutor(gold_root)
    try:
        system = build_system_prompt(gold_columns(gold_root, MONTH, list(TABLES)), month=MONTH, months=months)
        return rec.record(
            MONTH,
            months=months,
            llm=FakeLLM(*script),
            executor=executor,
            system=system,
            budget=Budget(limit_usd=1.0, period=MONTH),
            model="modelo-x",
        )
    finally:
        executor.close()


def test_every_data_example_has_an_answer_and_a_result_and_the_refusal_has_none(gold_root):
    document = run_record(gold_root, good_script())
    assert (document["esquema"], document["mes_referencia"], document["modelo"]) == (1, MONTH, "modelo-x")
    entries = document["exemplos"]
    single = [e for e in rec.EXAMPLES if not e.needs_months]
    assert [e["id"] for e in entries] == [e.id for e in single]
    assert [e["pergunta"] for e in entries] == [e.question for e in single]
    assert "meses" not in document  # com um mes so, as perguntas de varios meses nao entram
    for entry in entries[:4]:
        assert entry["status"] == "answered" and entry["resposta"].startswith("Resposta")
        assert entry["sql"].startswith("SELECT") and entry["colunas"] == ["uf", "empresas_ativas"]
        assert entry["linhas"] and entry["truncado"] is False
    refusal = entries[4]
    assert refusal["status"] == "out_of_scope" and refusal["resposta"]
    assert not ({"sql", "colunas", "linhas", "truncado"} & set(refusal))


def test_a_data_question_that_ends_without_an_answer_aborts_everything(gold_root):
    script = [turn("Não sei responder.")] + good_script()[2:]  # a primeira pergunta de dados vira recusa
    with pytest.raises(rec.UnexpectedOutcome, match="ranking_ufs"):
        run_record(gold_root, script)


def test_a_refusal_example_that_gets_answered_aborts_everything(gold_root):
    script = good_script()[:-1] + [turn("", GOOD_SQL), turn("Aqui estao os dados.")]
    with pytest.raises(rec.UnexpectedOutcome, match="fora_do_escopo"):
        run_record(gold_root, script)


def test_the_entry_keeps_only_the_last_successful_query_and_caps_the_rows():
    rows = [[f"x{i}", i] for i in range(rec.MAX_ROWS + 10)]
    answer = Answer(
        "answered",
        "texto",
        [
            QueryRecord(sql="SELECT 1", ok=True, columns=["a"], rows=[[1]]),
            QueryRecord(sql="SELECT 2", ok=True, columns=["a", "b"], rows=rows),
            QueryRecord(sql="SELECT 3", ok=False, error="erro"),
        ],
    )
    entry = rec.example_entry(rec.Example("x", "pergunta?"), answer)
    assert entry["sql"] == "SELECT 2" and len(entry["linhas"]) == rec.MAX_ROWS
    assert entry["truncado"] is True


def test_the_rendered_json_is_stable_and_has_no_timestamps(gold_root):
    first = rec.render(run_record(gold_root, good_script()))
    second = rec.render(run_record(gold_root, good_script()))
    assert first == second and first.endswith("}\n")
    assert not any(key in first for key in ("gerado_em", "generated", "timestamp", "data_hora"))


def prepare_main(gold_root: Path, tmp_path: Path, monkeypatch, script: list):
    out = tmp_path / "site"
    (out / MONTH).mkdir(parents=True)
    (out / MONTH / "index.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-falsa-de-teste")

    class Factory:
        def __init__(self, **kwargs):
            self.inner = FakeLLM(*script)

        def step(self, *args, **kwargs):
            return self.inner.step(*args, **kwargs)

    monkeypatch.setattr("cnpj_pipeline.agent.llm.AnthropicClient", Factory)
    return [
        "--month",
        MONTH,
        "--gold-dir",
        str(gold_root),
        "--out-dir",
        str(out),
    ], out / MONTH / rec.AGENT_EXAMPLES_FILE


def test_main_writes_the_examples_next_to_the_site_data(gold_root, tmp_path, monkeypatch, capsys):
    argv, path = prepare_main(gold_root, tmp_path, monkeypatch, good_script())
    assert rec.main(argv) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert len(document["exemplos"]) == len([e for e in rec.EXAMPLES if not e.needs_months])
    assert "exemplos gravados" in capsys.readouterr().out
    assert "chave-falsa" not in path.read_text(encoding="utf-8")


def test_main_writes_nothing_when_an_example_comes_out_wrong(gold_root, tmp_path, monkeypatch, capsys):
    script = [turn("Não sei responder.")] + good_script()[2:]
    argv, path = prepare_main(gold_root, tmp_path, monkeypatch, script)
    assert rec.main(argv) == 1
    assert not path.exists()
    assert "Nada foi gravado" in capsys.readouterr().err


def test_main_needs_the_key_in_the_environment_and_the_exported_site_data(
    gold_root, tmp_path, monkeypatch, capsys
):
    argv, path = prepare_main(gold_root, tmp_path, monkeypatch, good_script())
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert rec.main(argv) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-falsa-de-teste")
    (path.parent / "index.json").unlink()
    assert rec.main(argv) == 1 and "site_export" in capsys.readouterr().err
    assert not path.exists()


def test_a_refusal_never_carries_a_query_even_if_one_ran_before_it():
    done = QueryRecord(sql="SELECT 1", ok=True, columns=["a"], rows=[[1]])
    entry = rec.example_entry(
        rec.Example("x", "pergunta?", "out_of_scope"), Answer("out_of_scope", "nao", [done])
    )
    assert not ({"sql", "colunas", "linhas", "truncado"} & set(entry))


def test_an_answered_example_without_any_query_result_aborts(gold_root, monkeypatch):
    monkeypatch.setattr(rec, "ask", lambda *a, **k: Answer("answered", "texto solto", []))
    with pytest.raises(rec.UnexpectedOutcome, match="nenhum resultado"):
        run_record(gold_root, [])


def test_questions_that_compare_months_are_recorded_only_when_there_are_several_months(gold_root, tmp_path):
    import shutil

    root = tmp_path / "gold"
    shutil.copytree(gold_root, root)
    months = ("2026-09", MONTH)
    for model in root.iterdir():
        source = model / f"reference_month={MONTH}"
        if source.exists():
            shutil.copytree(source, model / "reference_month=2026-09")
    month_sql = (
        "SELECT reference_month, SUM(empresas_ativas) FROM gold_mart_simples_mei_uf GROUP BY reference_month"
    )
    script: list = []
    for example in rec.EXAMPLES:
        if example.expected_status == "out_of_scope":
            script.append(turn("Não posso informar dados de um CNPJ específico."))
        else:
            sql = month_sql if example.needs_months else GOOD_SQL
            script += [turn("", sql), turn(f"Resposta {example.id}.")]
    document = run_record(root, script, months)
    assert [e["id"] for e in document["exemplos"]] == [e.id for e in rec.EXAMPLES]
    assert document["meses"] == list(months)
    compared = [e for e in document["exemplos"] if e["id"] in {x.id for x in rec.EXAMPLES if x.needs_months}]
    assert compared and all("reference_month" in e["sql"] for e in compared)


def test_published_months_come_from_the_option_or_the_site_index(tmp_path):
    index = tmp_path / "months.json"
    assert rec.published_months(None, index) is None  # sem indice
    index.write_text(json.dumps({"meses": ["2026-07", "2026-10"]}), encoding="utf-8")
    assert rec.published_months(None, index) == ("2026-07", "2026-10")
    assert rec.published_months("2026-10, 2026-09", index) == ("2026-09", "2026-10")  # a opcao vence
    assert rec.published_months("2026-10", index) is None  # um mes so nao muda nada
    index.write_text(json.dumps({"meses": ["2026-10"]}), encoding="utf-8")
    assert rec.published_months(None, index) is None
