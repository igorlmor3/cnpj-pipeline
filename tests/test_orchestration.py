"""Testes da orquestracao: o que cada tarefa da DAG executa, sem precisar do Airflow."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from cnpj_pipeline import config, demo, orchestration, sample
from cnpj_pipeline.orchestration import (
    CLOUD_STAGES,
    MODE_FULL,
    MODE_SAMPLE,
    STAGES,
    OrchestrationError,
    RunConfig,
    build_argv,
    choose_source,
    layout,
    run_stage,
    should_publish,
)

FULL = RunConfig(mode=MODE_FULL, month="2026-10")
SAMPLE = RunConfig(mode=MODE_SAMPLE, month="2026-10")


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# --------------------------------------------------------------------------- #
# Parametros
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "26-10", "2026-1", "2026-10-01", "", "abcd-ef"])
def test_a_bad_month_is_rejected(month):
    with pytest.raises(OrchestrationError, match="mes invalido"):
        RunConfig(mode=MODE_FULL, month=month)


def test_a_bad_mode_is_rejected():
    with pytest.raises(OrchestrationError, match="modo invalido"):
        RunConfig(mode="producao")


def test_params_from_the_dag_become_a_config_with_booleans_parsed():
    cfg = RunConfig.from_params({"mode": "full", "month": "2026-09", "force": "True", "publish": "false"})
    assert cfg == RunConfig(mode="full", month="2026-09", force=True, publish=False)
    assert RunConfig.from_params({}) == RunConfig()  # padrao: amostra, sem force, sem publicar


def test_the_sample_always_uses_the_fictitious_month_and_rebuilds_every_time():
    assert SAMPLE.effective_month == sample.DEFAULT_MONTH == orchestration.SAMPLE_MONTH
    assert SAMPLE.effective_force is True
    assert FULL.effective_month == "2026-10" and FULL.effective_force is False
    assert RunConfig(mode=MODE_FULL, month="2026-10", force=True).effective_force is True


def test_the_sample_tolerances_are_the_ones_of_the_demo():
    assert float(orchestration.SAMPLE_MAX_REJECT_RATIO) == demo.DEMO_MAX_REJECT_RATIO
    assert float(orchestration.SAMPLE_MAX_QUARANTINE_RATIO) == demo.DEMO_MAX_QUARANTINE_RATIO


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #


def test_full_mode_reads_and_writes_the_real_folders_and_reports_go_to_docs():
    lay = layout(FULL)
    assert lay.base == config.DATA_DIR
    assert lay.raw == config.RAW_DIR and lay.bronze == config.BRONZE_DIR
    assert lay.silver == config.SILVER_DIR and lay.gold == config.GOLD_DIR
    assert lay.quality_report == config.PROJECT_ROOT / "docs" / "quality"
    assert lay.site_out == config.PROJECT_ROOT / "site" / "public" / "data"


def test_sample_mode_stays_inside_data_sample_and_never_touches_the_real_ones():
    lay = layout(SAMPLE)
    sample_root = config.DATA_DIR / "sample"
    for path in (
        lay.raw, lay.bronze, lay.silver, lay.gold, lay.quality,
        lay.silver_report, lay.gold_report, lay.quality_report, lay.site_out,
    ):  # fmt: skip
        assert sample_root in path.parents, path
    for stage in ("gerar_amostra", "bronze", "silver", "gold", "qualidade", "exportar_site"):
        argv = build_argv(stage, SAMPLE)
        paths = [Path(a) for a in argv[3:] if Path(a).is_absolute()]
        assert paths and all(sample_root in p.parents or p == sample_root for p in paths), (stage, argv)


# --------------------------------------------------------------------------- #
# Comandos de cada etapa
# --------------------------------------------------------------------------- #


def test_every_stage_runs_a_module_of_the_project_with_the_same_python():
    for stage in STAGES:
        cfg = FULL if stage not in ("gerar_amostra",) else SAMPLE
        argv = build_argv(stage, cfg)
        assert argv[:2] == [sys.executable, "-m"] and argv[2].startswith("cnpj_pipeline."), stage
        assert option(argv, "--month") == cfg.effective_month, stage


def test_the_full_flow_passes_the_chosen_month_and_the_real_folders():
    bronze = build_argv("bronze", FULL)
    assert option(bronze, "--raw-dir") == str(config.RAW_DIR)
    assert option(bronze, "--bronze-dir") == str(config.BRONZE_DIR)
    assert "--force" not in bronze and "--max-reject-ratio" not in bronze
    silver = build_argv("silver", FULL)
    assert option(silver, "--report-dir") == str(config.PROJECT_ROOT / "docs" / "silver")
    assert "--max-quarantine-ratio" not in silver
    gold = build_argv("gold", FULL)
    assert option(gold, "--gold-dir") == str(config.GOLD_DIR)
    quality = build_argv("qualidade", FULL)
    assert "--quick" not in quality  # a verificacao completa, nao a rapida
    assert option(quality, "--quality-dir") == str(config.DATA_DIR / "quality")


def test_force_is_forwarded_to_the_three_layers_in_full_mode_only_when_asked():
    forced = RunConfig(mode=MODE_FULL, month="2026-10", force=True)
    for stage in ("bronze", "silver", "gold"):
        assert "--force" in build_argv(stage, forced) and "--force" not in build_argv(stage, FULL)
    for stage in ("bronze", "silver", "gold"):
        assert "--force" in build_argv(stage, SAMPLE)  # a amostra sempre refaz


def test_the_sample_uses_the_demo_tolerances_and_a_small_memory_limit():
    assert option(build_argv("bronze", SAMPLE), "--max-reject-ratio") == orchestration.SAMPLE_MAX_REJECT_RATIO
    assert (
        option(build_argv("silver", SAMPLE), "--max-quarantine-ratio")
        == orchestration.SAMPLE_MAX_QUARANTINE_RATIO
    )
    for stage in ("bronze", "silver", "gold", "qualidade"):
        assert option(build_argv(stage, SAMPLE), "--memory-limit") == orchestration.SAMPLE_MEMORY_LIMIT
        assert "--memory-limit" not in build_argv(stage, FULL)


def test_the_source_stage_depends_on_the_mode():
    assert choose_source({"mode": "full"}) == "baixar_dados"
    assert choose_source({"mode": "sample"}) == "gerar_amostra"
    assert option(build_argv("baixar_dados", FULL), "--out-dir") == str(config.RAW_DIR)
    with pytest.raises(OrchestrationError, match="so existe no modo full"):
        build_argv("baixar_dados", SAMPLE)
    with pytest.raises(OrchestrationError, match="so existe no modo sample"):
        build_argv("gerar_amostra", FULL)


def test_the_cloud_stages_exist_only_in_full_mode_and_publish_only_the_gold():
    for stage in CLOUD_STAGES:
        with pytest.raises(OrchestrationError, match="so existe no modo full"):
            build_argv(stage, SAMPLE)
    publish = build_argv("publicar_s3", FULL)
    assert option(publish, "--layers") == "gold" and publish[2] == "cnpj_pipeline.cloud.publish"
    athena = build_argv("tabelas_athena", FULL)
    assert athena[2:6] == ["cnpj_pipeline.cloud.athena", "create-tables", "--layer", "gold"]


def test_publishing_needs_full_mode_and_the_explicit_flag():
    assert should_publish({"mode": "full", "publish": True}) is True
    assert should_publish({"mode": "full", "publish": False}) is False
    assert should_publish({"mode": "full"}) is False
    assert should_publish({"mode": "sample", "publish": True}) is False  # a amostra nunca vai para a nuvem


def test_an_unknown_stage_is_rejected():
    with pytest.raises(OrchestrationError, match="etapa desconhecida"):
        build_argv("profiling", FULL)


def test_the_site_export_of_the_sample_does_not_overwrite_the_real_site_data():
    assert option(build_argv("exportar_site", SAMPLE), "--out") != option(
        build_argv("exportar_site", FULL), "--out"
    )


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #


class FakeProcess:
    def __init__(self, lines: list[str], code: int):
        self.stdout = iter(lines)
        self.returncode = code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_popen(lines: list[str], code: int, seen: list[list[str]]):
    def popen(argv, **kwargs):
        seen.append(argv)
        assert kwargs["stderr"] == orchestration.subprocess.STDOUT  # um fluxo so, na ordem
        return FakeProcess(lines, code)

    return popen


def test_run_stage_runs_the_command_and_copies_its_output_to_the_log(monkeypatch, caplog):
    seen: list[list[str]] = []
    monkeypatch.setattr(orchestration.subprocess, "Popen", fake_popen(["linha 1\n", "linha 2\n"], 0, seen))
    with caplog.at_level(logging.INFO, logger="cnpj_pipeline.orchestration"):
        run_stage("gold", {"mode": "full", "month": "2026-10"})
    assert seen == [build_argv("gold", FULL)]
    assert "linha 1" in caplog.text and "linha 2" in caplog.text


def test_run_stage_fails_when_the_command_fails(monkeypatch):
    monkeypatch.setattr(orchestration.subprocess, "Popen", fake_popen(["erro\n"], 1, []))
    with pytest.raises(OrchestrationError, match="a etapa qualidade terminou com codigo 1"):
        run_stage("qualidade", {"mode": "full", "month": "2026-10"})


def test_run_stage_rejects_bad_parameters_before_running_anything(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(orchestration.subprocess, "Popen", fake_popen([], 0, seen))
    with pytest.raises(OrchestrationError, match="mes invalido"):
        run_stage("bronze", {"mode": "full", "month": "outubro"})
    assert seen == []


# --------------------------------------------------------------------------- #
# Ponta a ponta: o modo amostra de verdade, como a DAG o executa, etapa por etapa
# --------------------------------------------------------------------------- #


def test_the_sample_flow_runs_end_to_end_stage_by_stage(tmp_path, monkeypatch):
    """Roda as mesmas etapas da DAG, na ordem, com a pasta de dados apontada para um diretorio temporario
    (o subprocesso herda CNPJ_DATA_DIR). Prova que a ordem e as opcoes montadas aqui funcionam de fato."""
    monkeypatch.setenv("CNPJ_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    params = {"mode": "sample", "month": "2026-10"}
    for stage in ("gerar_amostra", "bronze", "silver", "gold", "qualidade", "exportar_site"):
        run_stage(stage, params)

    lay = layout(RunConfig.from_params(params))
    assert (lay.gold / "mart_simples_mei_uf").exists()
    assert (lay.quality_report / f"{orchestration.SAMPLE_MONTH}.md").exists()
    assert (lay.site_out / orchestration.SAMPLE_MONTH / "overview.json").exists()
    assert not (tmp_path / "raw").exists()  # nada fora de data/sample
