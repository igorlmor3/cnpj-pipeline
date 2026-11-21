"""Dispara o job Spark na AWS Glue (a partir da sua maquina), espera o fim e mostra tempo e custo.

    python experiments/glue_spark/run_job.py --profile cnpj-admin --month 2026-10 --workers 5 --verify

O job e o criado pelo Terraform (infra/modules/glue). `--verify` manda o job comparar o resultado com a
gold que o DuckDB publicou no S3. O custo mostrado e o calculado a partir dos DPU-segundos que a Glue
informa, ao preco de lista (US$ 0,44 por DPU-hora); o valor da fatura vem do Cost Explorer.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from cnpj_pipeline.cloud import common
from cnpj_pipeline.cloud.run_task import MAX_CONSECUTIVE_FAILURES, WAIT_DELAY_SECONDS, is_transient
from cnpj_pipeline.orchestration import MONTH_PATTERN

JOB_ENV = "CNPJ_GLUE_JOB"
DEFAULT_JOB = "cnpj-gold-spark"
PRICE_PER_DPU_HOUR = 0.44
DPUS_PER_WORKER = {"G.1X": 1, "G.2X": 2, "G.4X": 4, "G.8X": 8}
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"})
MINIMUM_BILLED_SECONDS = 60


class JobError(RuntimeError):
    pass


def build_arguments(month: str, *, verify_bucket: str | None) -> dict[str, str]:
    """Argumentos do job. A conferencia aponta para a gold publicada pelo DuckDB no mesmo bucket."""
    arguments = {"--month": month}
    if verify_bucket:
        arguments["--verify-dir"] = f"s3://{verify_bucket}/gold"
    return arguments


def start(client: Any, *, job_name: str, workers: int, worker_type: str, arguments: dict[str, str]) -> str:
    response = client.start_job_run(
        JobName=job_name, Arguments=arguments, WorkerType=worker_type, NumberOfWorkers=workers
    )
    run_id = response.get("JobRunId")
    if not run_id:
        raise JobError("a Glue nao devolveu o identificador da execucao")
    return str(run_id)


def wait_for_run(
    client: Any,
    job_name: str,
    run_id: str,
    *,
    timeout_minutes: int,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Espera a execucao chegar a um estado final e devolve o JobRun. Falhas de rede sao repetidas."""
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    deadline = monotonic() + timeout_minutes * 60
    failures = 0
    while True:
        try:
            run = client.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
            failures = 0
        except Exception as error:
            if not is_transient(error):
                raise
            failures += 1
            if failures > MAX_CONSECUTIVE_FAILURES:
                raise JobError(
                    f"sem resposta da Glue depois de {failures} consultas seguidas; "
                    "a execucao pode continuar rodando na AWS"
                ) from error
            print(
                f"Aviso: consulta a Glue falhou ({type(error).__name__}); tentando de novo", file=sys.stderr
            )
        else:
            if run.get("JobRunState") in TERMINAL_STATES:
                return run
            if monotonic() >= deadline:
                raise JobError(f"a execucao seguia rodando depois de {timeout_minutes} minutos de espera")
        sleep(WAIT_DELAY_SECONDS)


def summarize(run: dict[str, Any], *, workers: int, worker_type: str) -> dict[str, Any]:
    """Estado, tempo e custo de uma execucao terminada."""
    seconds = int(run.get("ExecutionTime") or 0)
    dpu_seconds = run.get("DPUSeconds")
    estimated = dpu_seconds is None
    if estimated:
        dpu_seconds = max(seconds, MINIMUM_BILLED_SECONDS) * workers * DPUS_PER_WORKER.get(worker_type, 1)
    return {
        "estado": run.get("JobRunState"),
        "segundos": seconds,
        "dpu_segundos": float(dpu_seconds),
        "dpu_segundos_estimados": estimated,
        "custo_usd": round(float(dpu_seconds) / 3600 * PRICE_PER_DPU_HOUR, 4),
        "erro": run.get("ErrorMessage"),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispara o job Spark na AWS Glue")
    parser.add_argument("--month", required=True, help="Mes de referencia AAAA-MM")
    parser.add_argument("--workers", type=int, default=5, help="Numero de workers (minimo 2; um e o driver)")
    parser.add_argument("--worker-type", default="G.1X", choices=sorted(DPUS_PER_WORKER))
    parser.add_argument("--verify", action="store_true", help="Compara o resultado com a gold do DuckDB")
    parser.add_argument("--no-wait", action="store_true", help="Inicia a execucao e retorna")
    parser.add_argument("--timeout-minutes", type=int, default=90, help="Limite da espera deste comando")
    parser.add_argument("--job-name", default=os.environ.get(JOB_ENV, DEFAULT_JOB))
    parser.add_argument("--bucket", default=common.default_bucket())
    parser.add_argument("--profile", default=common.default_profile())
    parser.add_argument("--region", default=common.default_region())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, client: Any | None = None) -> int:
    args = parse_args(argv)
    try:
        if not MONTH_PATTERN.fullmatch(args.month):
            raise JobError("--month precisa estar no formato AAAA-MM")
        if args.workers < 2:
            raise JobError("--workers precisa ser pelo menos 2 (a Glue usa um worker como driver)")
        if args.timeout_minutes <= 0:
            raise JobError("--timeout-minutes precisa ser maior que zero")
        if args.verify and not args.bucket:
            raise JobError("--verify precisa do bucket: informe --bucket ou a variavel CNPJ_S3_BUCKET")
        glue = client or common.make_client("glue", profile=args.profile, region=args.region)
        run_id = start(
            glue,
            job_name=args.job_name,
            workers=args.workers,
            worker_type=args.worker_type,
            arguments=build_arguments(args.month, verify_bucket=args.bucket if args.verify else None),
        )
    except (JobError, common.CloudConfigError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2

    print(f"Execucao iniciada: {run_id} ({args.workers} workers {args.worker_type}, mes {args.month})")
    if args.no_wait:
        return 0
    try:
        run = wait_for_run(glue, args.job_name, run_id, timeout_minutes=args.timeout_minutes)
    except JobError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        print(
            f"Veja a execucao com: aws glue get-job-run --job-name {args.job_name} --run-id {run_id} "
            "--profile <perfil>",
            file=sys.stderr,
        )
        return 3
    result = summarize(run, workers=args.workers, worker_type=args.worker_type)
    print(f"Estado: {result['estado']}")
    print(f"Tempo de execucao: {result['segundos']} s")
    origin = "estimado" if result["dpu_segundos_estimados"] else "informado pela Glue"
    print(f"DPU-segundos: {result['dpu_segundos']:.0f} ({origin})")
    print(f"Custo a preco de lista: US$ {result['custo_usd']:.4f}")
    if result["erro"]:
        print(f"Erro: {result['erro']}", file=sys.stderr)
    return 0 if result["estado"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
