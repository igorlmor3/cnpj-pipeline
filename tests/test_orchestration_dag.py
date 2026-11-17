"""Integridade da DAG do Airflow. Precisa do Airflow instalado; sem ele, os testes sao pulados.

O CI instala o Airflow num job proprio (veja .github/workflows/ci.yml). Localmente, a mesma checagem
roda dentro do contêiner: docker compose -f orchestration/docker-compose.yml exec airflow-scheduler
pytest /opt/cnpj/tests/test_orchestration_dag.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("airflow.sdk")

from airflow.models import DagBag  # noqa: E402

from cnpj_pipeline import orchestration  # noqa: E402

DAG_DIR = Path(__file__).resolve().parents[1] / "orchestration" / "dags"


@pytest.fixture(scope="module")
def dag():
    bag = DagBag(dag_folder=str(DAG_DIR))
    assert bag.import_errors == {}
    return bag.dags["cnpj_monthly"]


def upstream_ids(dag, task_id: str) -> set[str]:
    return dag.get_task(task_id).get_flat_relative_ids(upstream=True)


def test_the_dag_has_every_stage_of_the_flow(dag):
    stage_tasks = {t.task_id for t in dag.tasks} - {"escolher_origem", "publicar_ativo"}
    assert stage_tasks == set(orchestration.STAGES)  # uma tarefa por etapa, com o mesmo id
    assert {t.task_id for t in dag.tasks} == set(orchestration.STAGES) | {"escolher_origem", "publicar_ativo"}


def test_the_flow_runs_in_the_documented_order(dag):
    edges = {t.task_id: set(t.downstream_task_ids) for t in dag.tasks}
    assert edges["escolher_origem"] == {"baixar_dados", "gerar_amostra"}
    assert edges["baixar_dados"] == edges["gerar_amostra"] == {"bronze"}
    assert edges["bronze"] == {"silver"} and edges["silver"] == {"gold"} and edges["gold"] == {"qualidade"}
    assert edges["qualidade"] == {"exportar_site", "publicar_ativo"}
    assert edges["publicar_ativo"] == {"publicar_s3"} and edges["publicar_s3"] == {"tabelas_athena"}


def test_nothing_is_exported_or_published_without_passing_the_quality_gate(dag):
    for task_id in ("exportar_site", "publicar_ativo", "publicar_s3", "tabelas_athena"):
        assert "qualidade" in upstream_ids(dag, task_id), task_id
    assert "gold" in upstream_ids(dag, "qualidade")


def test_the_branch_does_not_block_the_bronze_when_one_source_is_skipped(dag):
    assert dag.get_task("bronze").trigger_rule == "none_failed_min_one_success"
    assert dag.get_task("qualidade").trigger_rule == "all_success"  # uma falha anterior barra o portao


def test_the_parameters_default_to_the_safe_sample_without_publishing(dag):
    params = dag.params
    assert params["mode"] == "sample" and params["publish"] is False and params["force"] is False
    assert orchestration.MONTH_PATTERN.fullmatch(params["month"])
    assert dag.schedule is None  # nunca dispara sozinho: o mes real leva horas
    assert dag.max_active_runs == 1  # as execucoes compartilham as pastas de dados


def test_each_stage_task_runs_the_matching_stage(dag):
    for stage in orchestration.STAGES:
        task = dag.get_task(stage)
        assert task.python_callable is orchestration.run_stage
        assert task.op_kwargs == {"stage": stage}


def test_the_download_retries_and_the_other_stages_do_not(dag):
    assert dag.get_task("baixar_dados").retries == 2
    for stage in ("bronze", "silver", "gold", "qualidade"):
        assert dag.get_task(stage).retries == 0  # falha de dado nao melhora repetindo
