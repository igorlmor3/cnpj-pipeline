"""Dispara a tarefa do pipeline no ECS Fargate (a partir da sua maquina) e, se pedido, espera o fim.

    python -m cnpj_pipeline.cloud.run_task --month 2026-11 --wait
    python -m cnpj_pipeline.cloud.run_task --probe          # so confere o acesso a Receita a partir da AWS

Os dados da tarefa (cluster, definicao, sub-redes e grupo de seguranca) saem do `terraform output`; veja
infra/README.md. Eles podem vir por argumento ou pelas variaveis CNPJ_ECS_CLUSTER, CNPJ_ECS_TASK_DEFINITION,
CNPJ_ECS_SUBNETS (separadas por virgula) e CNPJ_ECS_SECURITY_GROUP.

A tarefa so e iniciada depois que os argumentos sao validados. Nenhum segredo trafega aqui: as credenciais da
tarefa vem do papel IAM dela.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from cnpj_pipeline.cloud import common
from cnpj_pipeline.cloud.task import FORCE_ENV, MONTHS_ENV, TIMEOUT_ENV, TaskConfigError, parse_months
from cnpj_pipeline.orchestration import MONTH_PATTERN

CONTAINER_NAME = "pipeline"
WAIT_DELAY_SECONDS = 30
# Consultas seguidas ao ECS que podem falhar antes de desistir de esperar (20 x 30 s = 10 minutos sem rede).
MAX_CONSECUTIVE_FAILURES = 20
# Erros do botocore que indicam rede ou limite de requisicoes, e nao um pedido errado.
TRANSIENT_BOTOCORE_ERRORS = frozenset(
    {
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "EndpointConnectionError",
        "ConnectionClosedError",
        "ProxyConnectionError",
        "SSLError",
    }
)
THROTTLING_CODES = frozenset({"ThrottlingException", "RequestLimitExceeded", "ServiceUnavailableException"})


class LaunchError(RuntimeError):
    pass


def split_list(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").replace(";", ",").split(",") if item.strip()]


def build_overrides(
    *, months: Sequence[str], probe: bool, force: bool, timeout_minutes: int | None
) -> dict[str, Any]:
    """Sobrescritas do contêiner: os meses, o limite de tempo e, no teste de acesso, o comando."""
    environment = [{"name": MONTHS_ENV, "value": " ".join(months)}]
    if force:
        environment.append({"name": FORCE_ENV, "value": "1"})
    if timeout_minutes is not None:
        environment.append({"name": TIMEOUT_ENV, "value": str(timeout_minutes)})
    container: dict[str, Any] = {"name": CONTAINER_NAME, "environment": environment}
    if probe:
        container["command"] = ["python", "-m", "cnpj_pipeline.cloud.task", "probe", "--month", months[0]]
    return {"containerOverrides": [container]}


def start(
    client: Any,
    *,
    cluster: str,
    task_definition: str,
    subnets: Sequence[str],
    security_group: str,
    overrides: dict[str, Any],
) -> str:
    """Inicia uma unica tarefa e devolve o ARN dela."""
    response = client.run_task(
        cluster=cluster,
        taskDefinition=task_definition,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": list(subnets),
                "securityGroups": [security_group],
                # Sem NAT Gateway, a tarefa sai para a internet por IP publico: o grupo de seguranca
                # nao aceita nenhuma entrada.
                "assignPublicIp": "ENABLED",
            }
        },
        overrides=overrides,
    )
    failures = response.get("failures") or []
    if failures:
        reasons = "; ".join(
            f"{f.get('reason', 'sem motivo')} ({f.get('detail', '')})".strip() for f in failures
        )
        raise LaunchError(f"o ECS recusou iniciar a tarefa: {reasons}")
    tasks = response.get("tasks") or []
    if len(tasks) != 1:
        raise LaunchError(f"resposta inesperada do ECS: {len(tasks)} tarefas iniciadas")
    return str(tasks[0]["taskArn"])


def is_transient(error: BaseException) -> bool:
    """Falha que passa sozinha (rede fora do ar, limite de requisicoes), sem importar o botocore."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    if type(error).__module__.startswith("botocore") and type(error).__name__ in TRANSIENT_BOTOCORE_ERRORS:
        return True
    response = getattr(error, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in THROTTLING_CODES


def wait_for_exit(
    client: Any,
    cluster: str,
    task_arn: str,
    *,
    timeout_minutes: int,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> int:
    """Espera a tarefa parar e devolve o codigo de saida do contêiner (1 se nao houver).

    Consulta o ECS a cada WAIT_DELAY_SECONDS. Uma consulta que falha por rede ou por limite de requisicoes
    e repetida (a tarefa continua rodando na AWS, e uma queda de rede na maquina de quem espera nao deve
    derrubar a espera). So depois de MAX_CONSECUTIVE_FAILURES falhas seguidas a espera termina com erro.
    """
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    deadline = monotonic() + timeout_minutes * 60
    failures = 0
    while True:
        try:
            described = client.describe_tasks(cluster=cluster, tasks=[task_arn])
            tasks = described.get("tasks") or []
            problem = None if tasks else "o ECS nao devolveu a tarefa"
        except Exception as error:
            if not is_transient(error):
                raise
            tasks, problem = [], f"{type(error).__name__}"
        if problem is None:
            failures = 0
            task = tasks[0]
            if task.get("lastStatus") == "STOPPED":
                break
            if monotonic() >= deadline:
                raise LaunchError(f"a tarefa seguia rodando depois de {timeout_minutes} minutos de espera")
        else:
            failures += 1
            if failures > MAX_CONSECUTIVE_FAILURES:
                raise LaunchError(
                    f"sem resposta do ECS depois de {failures} consultas seguidas ({problem}); "
                    "a tarefa pode continuar rodando na AWS"
                )
            print(f"Aviso: consulta ao ECS falhou ({problem}); tentando de novo", file=sys.stderr)
        sleep(WAIT_DELAY_SECONDS)
    containers = task.get("containers") or []
    code = containers[0].get("exitCode") if containers else None
    if code is None:
        print(
            f"A tarefa parou sem codigo de saida: {task.get('stoppedReason', 'motivo nao informado')}",
            file=sys.stderr,
        )
        return 1
    return int(code)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    env = os.environ
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.cloud.run_task",
        description="Dispara a tarefa do pipeline no ECS Fargate",
    )
    parser.add_argument("--month", nargs="+", metavar="AAAA-MM", help="Meses (padrao: o mes corrente)")
    parser.add_argument(
        "--probe", action="store_true", help="Confere o acesso a Receita em vez de rodar o pipeline"
    )
    parser.add_argument("--force", action="store_true", help="Refaz bronze, silver e gold que ja existem")
    parser.add_argument(
        "--timeout-minutes", type=int, help="Limite da execucao (padrao: o da definicao da tarefa)"
    )
    parser.add_argument(
        "--wait", action="store_true", help="Espera a tarefa terminar e devolve o codigo dela"
    )
    parser.add_argument("--cluster", default=env.get("CNPJ_ECS_CLUSTER"))
    parser.add_argument("--task-definition", default=env.get("CNPJ_ECS_TASK_DEFINITION"))
    parser.add_argument(
        "--subnets", default=env.get("CNPJ_ECS_SUBNETS"), help="Sub-redes separadas por virgula"
    )
    parser.add_argument("--security-group", default=env.get("CNPJ_ECS_SECURITY_GROUP"))
    parser.add_argument("--profile", default=common.default_profile())
    parser.add_argument("--region", default=common.default_region())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, client: Any | None = None) -> int:
    args = parse_args(argv)
    try:
        from datetime import UTC, datetime

        months = parse_months(" ".join(args.month or []), datetime.now(UTC).date())
        missing = [
            name
            for name, value in (
                ("--cluster", args.cluster),
                ("--task-definition", args.task_definition),
                ("--subnets", args.subnets),
                ("--security-group", args.security_group),
            )
            if not value
        ]
        if missing:
            raise LaunchError(
                f"informe {', '.join(missing)} (ou as variaveis CNPJ_ECS_*; veja infra/README.md)"
            )
        if args.timeout_minutes is not None and args.timeout_minutes <= 0:
            raise LaunchError("--timeout-minutes precisa ser maior que zero")
        if not all(MONTH_PATTERN.fullmatch(m) for m in months):
            raise LaunchError("mes invalido")
        overrides = build_overrides(
            months=months, probe=args.probe, force=args.force, timeout_minutes=args.timeout_minutes
        )
        ecs = client or common.make_client("ecs", profile=args.profile, region=args.region)
        arn = start(
            ecs,
            cluster=args.cluster,
            task_definition=args.task_definition,
            subnets=split_list(args.subnets),
            security_group=args.security_group,
            overrides=overrides,
        )
    except (TaskConfigError, LaunchError, common.CloudConfigError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2

    task_id = arn.rsplit("/", 1)[-1]
    kind = "teste de acesso" if args.probe else f"pipeline de {' '.join(months)}"
    print(f"Tarefa iniciada ({kind}): {task_id}")
    print(f"Acompanhe os logs: aws logs tail /ecs/{args.cluster} --follow --profile <perfil>")
    if not args.wait:
        return 0
    wait_minutes = args.timeout_minutes or 400
    try:
        code = wait_for_exit(ecs, args.cluster, arn, timeout_minutes=wait_minutes + 10)
    except LaunchError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        print(
            f"Veja o estado da tarefa com: aws ecs describe-tasks --cluster {args.cluster} "
            f"--tasks {task_id} --profile <perfil>",
            file=sys.stderr,
        )
        return 3
    print("Tarefa concluida com sucesso" if code == 0 else f"Tarefa terminou com codigo {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
