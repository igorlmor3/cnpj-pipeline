"""Testes da orquestracao: o que cada tarefa da DAG executa, sem precisar do Airflow."""

from __future__ import annotations

import itertools
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
    FlowFailed,
    OrchestrationError,
    RunConfig,
    build_argv,
    choose_source,
    flow,
    format_duration,
    layout,
    main,
    run_flow,
    run_stage,
    should_publish,
    summary,
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


def test_skip_site_comes_from_the_dag_params_and_is_off_by_default():
    assert RunConfig.from_params({"mode": "full", "skip_site": "true"}).skip_site is True
    assert RunConfig.from_params({"mode": "full", "skip_site": "false"}).skip_site is False
    assert RunConfig.from_params({}).skip_site is False


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


# --------------------------------------------------------------------------- #
# Varios meses, sem o Airflow
# --------------------------------------------------------------------------- #


class Recorder:
    """Runner de mentira: anota (mes, etapa) e pode falhar em uma etapa de um mes."""

    def __init__(self, fail_at: tuple[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_at = fail_at

    def __call__(self, stage: str, params) -> None:
        self.calls.append((params["month"], stage))
        if self.fail_at == (params["month"], stage):
            raise OrchestrationError(f"a etapa {stage} terminou com codigo 1")


def test_the_flow_of_a_full_month_goes_from_the_download_to_the_site_export():
    assert flow(FULL) == ["baixar_dados", "bronze", "silver", "gold", "qualidade", "exportar_site"]
    assert flow(SAMPLE)[0] == "gerar_amostra"


def test_the_cloud_stages_join_the_flow_only_when_publishing_in_full_mode():
    assert flow(RunConfig(mode=MODE_FULL, month="2026-10", publish=True))[-2:] == list(CLOUD_STAGES)
    assert not set(CLOUD_STAGES) & set(flow(RunConfig(mode=MODE_SAMPLE, month="2026-10", publish=True)))


def test_skipping_the_site_removes_only_the_export_and_keeps_the_cloud_stages():
    cfg = RunConfig(mode=MODE_FULL, month="2026-10", publish=True, skip_site=True)
    assert flow(cfg) == ["baixar_dados", "bronze", "silver", "gold", "qualidade", *CLOUD_STAGES]
    assert "exportar_site" not in flow(RunConfig(mode=MODE_FULL, month="2026-10", skip_site=True))
    assert flow(FULL)[-1] == "exportar_site"  # sem a opcao, tudo continua como antes


def test_run_flow_with_skip_site_never_runs_the_export():
    runner = Recorder()
    run_flow(["2026-07", "2026-08"], skip_site=True, runner=runner)
    assert [stage for _, stage in runner.calls].count("exportar_site") == 0
    assert [stage for _, stage in runner.calls].count("qualidade") == 2


def test_each_month_runs_the_whole_flow_before_the_next_one_starts():
    runner = Recorder()
    results = run_flow(["2026-07", "2026-08"], runner=runner)
    expected = [(m, s) for m in ("2026-07", "2026-08") for s in flow(FULL)]
    assert runner.calls == expected
    assert [(r.month, r.stage) for r in results] == expected


def test_the_stage_durations_are_measured_with_the_clock():
    ticks = itertools.count(0, 5)
    results = run_flow(["2026-07"], runner=Recorder(), clock=lambda: float(next(ticks)))
    assert {r.seconds for r in results} == {5.0}


def test_a_failure_stops_everything_and_says_where_it_happened():
    runner = Recorder(fail_at=("2026-08", "silver"))
    with pytest.raises(FlowFailed) as info:
        run_flow(["2026-07", "2026-08", "2026-09"], runner=runner)
    assert (info.value.month, info.value.stage) == ("2026-08", "silver")
    assert ("2026-09", "baixar_dados") not in runner.calls  # o mes seguinte nem comecou
    assert [(r.month, r.stage) for r in info.value.completed][-1] == ("2026-08", "bronze")


def test_resuming_starts_the_first_month_at_the_given_stage_and_the_others_from_the_start():
    runner = Recorder()
    run_flow(["2026-08", "2026-09"], from_stage="gold", runner=runner)
    assert runner.calls[0] == ("2026-08", "gold")
    assert ("2026-08", "bronze") not in runner.calls
    assert ("2026-09", "baixar_dados") in runner.calls


def test_bad_arguments_are_rejected_before_anything_runs():
    runner = Recorder()
    for months, extra, message in [
        ([], {}, "pelo menos um mes"),
        (["2026-07", "2026-07"], {}, "repetido"),
        (["2026-07", "2026-13"], {}, "mes invalido"),
        (["2026-07"], {"mode": "sample", "from_stage": "baixar_dados"}, "nao faz parte do fluxo"),
        (["2026-07"], {"from_stage": "publicar_s3"}, "nao faz parte do fluxo"),
    ]:
        with pytest.raises(OrchestrationError, match=message):
            run_flow(months, runner=runner, **extra)
    assert runner.calls == []


def test_durations_are_written_in_a_readable_way():
    assert format_duration(8.24) == "8,2 s"
    assert format_duration(185) == "3 min 05 s"
    assert format_duration(3720) == "1 h 02 min"


def test_the_summary_lists_every_stage_and_the_total_per_month():
    results = run_flow(["2026-07", "2026-08"], runner=Recorder(), clock=itertools.count(0, 60).__next__)
    text = summary(results)
    assert "2026-07  gold" in text and "2026-08  exportar_site" in text
    assert text.count("total") == 2


def test_main_returns_zero_and_prints_the_summary(monkeypatch, capsys):
    monkeypatch.setattr(orchestration, "run_stage", Recorder())
    monkeypatch.setattr(orchestration, "run_flow", lambda *a, **k: run_flow(*a, runner=Recorder(), **k))
    assert main(["--months", "2026-09"]) == 0
    assert "2026-09  silver" in capsys.readouterr().out


def test_main_forwards_skip_site_to_the_flow(monkeypatch):
    seen = {}

    def fake_flow(months, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(orchestration, "run_flow", fake_flow)
    assert main(["--months", "2026-09", "--skip-site"]) == 0
    assert seen["skip_site"] is True
    assert main(["--months", "2026-09"]) == 0
    assert seen["skip_site"] is False


def test_main_tells_how_to_resume_after_a_failure(monkeypatch, capsys):
    runner = Recorder(fail_at=("2026-08", "gold"))
    monkeypatch.setattr(orchestration, "run_flow", lambda *a, **k: run_flow(*a, runner=runner, **k))
    assert main(["--months", "2026-07", "2026-08", "2026-09"]) == 1
    err = capsys.readouterr().err
    assert "FALHOU: 2026-08, etapa gold" in err
    assert "--months 2026-08 2026-09 --from-stage gold" in err


def test_main_reports_invalid_arguments_with_exit_code_two(capsys):
    assert main(["--months", "2026-99"]) == 2
    assert "mes invalido" in capsys.readouterr().err
