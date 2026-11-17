"""Fluxo mensal do pipeline do CNPJ: dados brutos, bronze, silver, gold, qualidade, site e AWS.

Cada tarefa executa um modulo do projeto em um processo separado (`cnpj_pipeline.orchestration`
monta e roda a linha de comando); a DAG so define a ordem e o que acontece quando algo falha.

Parametros (Trigger DAG w/ config):
- mode: `sample` (dados sinteticos em data/sample, roda em segundos) ou `full` (o mes real).
- month: mes de referencia AAAA-MM, usado no modo `full` (a amostra usa um mes ficticio).
- force: refaz bronze, silver e gold que ja existem (a amostra sempre refaz).
- publish: no modo `full`, publica a gold no S3 e cria as tabelas no Athena. Exige credenciais AWS.

Fluxo: escolher_origem -> baixar_dados | gerar_amostra -> bronze -> silver -> gold -> qualidade ->
exportar_site e publicar_s3 -> tabelas_athena. A tarefa `qualidade` e o portao: se uma verificacao
bloqueante falhar, nada e exportado nem publicado.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.python import (
    BranchPythonOperator,
    PythonOperator,
    ShortCircuitOperator,
)
from airflow.sdk import DAG, Param

from cnpj_pipeline import orchestration

# O mes real tem 26,9 GiB de CSV: cada etapa pesada ganha um teto de tempo generoso, para uma
# tarefa travada nao ocupar a maquina para sempre.
HEAVY = timedelta(hours=12)


def pick_source(params, **_):
    return orchestration.choose_source(params)


def publish_enabled(params, **_):
    return orchestration.should_publish(params)


def stage(task_id: str, **kwargs) -> PythonOperator:
    return PythonOperator(
        task_id=task_id,
        python_callable=orchestration.run_stage,
        op_kwargs={"stage": task_id},
        **kwargs,
    )


with DAG(
    dag_id="cnpj_monthly",
    description="Pipeline do CNPJ: raw, bronze, silver, gold, qualidade, site e AWS",
    doc_md=__doc__,
    schedule=None,  # disparo manual: o mes e a escolha do modo vem dos parametros
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,  # as execucoes compartilham as mesmas pastas de dados
    tags=["cnpj", "etl", "duckdb"],
    params={
        "mode": Param(
            "sample",
            type="string",
            enum=list(orchestration.MODES),
            description="sample: dados sinteticos em segundos. full: o mes real, em horas.",
        ),
        "month": Param(
            "2026-10",
            type="string",
            pattern=orchestration.MONTH_PATTERN.pattern,
            description="Mes de referencia AAAA-MM (modo full).",
        ),
        "force": Param(False, type="boolean", description="Refaz camadas que ja existem (modo full)."),
        "publish": Param(
            False, type="boolean", description="Publica a gold no S3 e no Athena (modo full, exige AWS)."
        ),
    },
    default_args={"retries": 0},
) as dag:
    escolher_origem = BranchPythonOperator(task_id="escolher_origem", python_callable=pick_source)

    baixar_dados = stage("baixar_dados", retries=2, retry_delay=timedelta(minutes=5), execution_timeout=HEAVY)
    gerar_amostra = stage("gerar_amostra")

    # Depois do desvio, uma das duas origens e pulada; as etapas seguintes so exigem que nenhuma falhou.
    bronze = stage("bronze", trigger_rule="none_failed_min_one_success", execution_timeout=HEAVY)
    silver = stage("silver", execution_timeout=HEAVY)
    gold = stage("gold", execution_timeout=HEAVY)
    qualidade = stage("qualidade", execution_timeout=HEAVY)

    exportar_site = stage("exportar_site")
    publicar_ativo = ShortCircuitOperator(task_id="publicar_ativo", python_callable=publish_enabled)
    publicar_s3 = stage("publicar_s3", execution_timeout=HEAVY)
    tabelas_athena = stage("tabelas_athena")

    escolher_origem >> [baixar_dados, gerar_amostra] >> bronze >> silver >> gold >> qualidade
    qualidade >> [exportar_site, publicar_ativo]
    publicar_ativo >> publicar_s3 >> tabelas_athena
