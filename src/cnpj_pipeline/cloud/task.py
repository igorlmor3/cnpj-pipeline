"""Ponto de entrada da tarefa que roda o pipeline na nuvem (ECS Fargate).

Dois subcomandos:

    python -m cnpj_pipeline.cloud.task run     # baixa, processa e publica a gold do mes
    python -m cnpj_pipeline.cloud.task probe   # so confere se o servidor da Receita responde a este ambiente

A configuracao vem de variaveis de ambiente, definidas na definicao da tarefa (Terraform) ou
sobrescritas no disparo (`python -m cnpj_pipeline.cloud.run_task`):

    CNPJ_MONTHS                  meses AAAA-MM separados por espaco ou virgula; vazio = mes corrente (UTC)
    CNPJ_S3_BUCKET               bucket de destino da gold (obrigatorio em `run`)
    CNPJ_TASK_TIMEOUT_MINUTES    tempo maximo; passado o limite, a execucao e encerrada (padrao 360)
    CNPJ_MIN_FREE_GIB            espaco livre minimo no disco de trabalho antes de comecar (padrao 60)
    CNPJ_FORCE                   1/true refaz bronze, silver e gold que ja existem

O tempo maximo existe porque o ECS nao tem limite de duracao para uma tarefa: sem ele, uma etapa
travada seguiria cobrando ate alguem desligar.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import requests

from cnpj_pipeline import config
from cnpj_pipeline.cloud import common
from cnpj_pipeline.download import ZIP_SIGNATURES, build_url, resolve_auth
from cnpj_pipeline.orchestration import MONTH_PATTERN

MONTHS_ENV = "CNPJ_MONTHS"
TIMEOUT_ENV = "CNPJ_TASK_TIMEOUT_MINUTES"
MIN_FREE_ENV = "CNPJ_MIN_FREE_GIB"
FORCE_ENV = "CNPJ_FORCE"

DEFAULT_TIMEOUT_MINUTES = 360
DEFAULT_MIN_FREE_GIB = 60.0
TIMEOUT_EXIT_CODE = 124  # o mesmo codigo do comando `timeout` do shell
CONFIG_EXIT_CODE = 2

GIB = 1024**3


class TaskConfigError(ValueError):
    """Configuracao ausente ou invalida na tarefa."""


def parse_months(raw: str | None, today: date) -> list[str]:
    """Meses da execucao. Vazio vale o mes corrente: a Receita publica a pasta do mes no proprio mes."""
    text = (raw or "").replace(",", " ").split()
    months = text or [f"{today.year:04d}-{today.month:02d}"]
    for month in months:
        if not MONTH_PATTERN.fullmatch(month):
            raise TaskConfigError(f"mes invalido em {MONTHS_ENV}: {month!r} (formato AAAA-MM)")
    if len(set(months)) != len(months):
        raise TaskConfigError(f"mes repetido em {MONTHS_ENV}")
    return months


def _positive_number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise TaskConfigError(f"{name} precisa ser um numero: {raw!r}") from exc
    if value <= 0:
        raise TaskConfigError(f"{name} precisa ser maior que zero: {raw!r}")
    return value


def orchestration_argv(months: Sequence[str], *, force: bool) -> list[str]:
    """Comando da execucao: modo completo, com publicacao e sem a exportacao do site."""
    argv = [sys.executable, "-m", "cnpj_pipeline.orchestration", "--mode", "full", "--months", *months]
    argv += ["--publish", "--skip-site"]
    if force:
        argv.append("--force")
    return argv


def run_task(
    env: Mapping[str, str],
    *,
    today: date,
    runner: Callable[..., Any] = subprocess.run,
    disk_usage: Callable[[Any], Any] = shutil.disk_usage,
) -> int:
    try:
        months = parse_months(env.get(MONTHS_ENV), today)
        timeout = _positive_number(env, TIMEOUT_ENV, DEFAULT_TIMEOUT_MINUTES)
        min_free = _positive_number(env, MIN_FREE_ENV, DEFAULT_MIN_FREE_GIB)
        if not (env.get(common.BUCKET_ENV) or "").strip():
            raise TaskConfigError(f"{common.BUCKET_ENV} nao esta definido")
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        free = disk_usage(config.DATA_DIR).free / GIB
        if free < min_free:
            raise TaskConfigError(
                f"espaco livre insuficiente em {config.DATA_DIR}: {free:.1f} GiB, minimo {min_free:.0f} GiB"
            )
    except TaskConfigError as error:
        print(f"CONFIGURACAO INVALIDA: {error}", file=sys.stderr)
        return CONFIG_EXIT_CODE

    force = env.get(FORCE_ENV, "").strip().casefold() in ("1", "true", "sim", "yes")
    print(
        f"Iniciando: meses {' '.join(months)}, limite de {timeout:g} min, {free:.0f} GiB livres, "
        f"destino s3://{env[common.BUCKET_ENV].strip()}",
        flush=True,
    )
    try:
        completed = runner(orchestration_argv(months, force=force), timeout=timeout * 60, check=False)
    except subprocess.TimeoutExpired:
        print(f"TEMPO ESGOTADO: a execucao passou de {timeout:g} min e foi encerrada", file=sys.stderr)
        return TIMEOUT_EXIT_CODE
    return int(completed.returncode)


def probe(
    month: str,
    *,
    url_template: str,
    share_token: str | None = None,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> int:
    """Pede os primeiros bytes de um arquivo pequeno e confere se e um zip.

    O firewall da Receita responde 200 com uma pagina HTML de recusa, entao o status sozinho nao basta: o
    que prova o acesso e a assinatura de zip nos primeiros bytes.
    """
    url = build_url(url_template, month, "Cnaes.zip")
    auth = resolve_auth(url_template, share_token)
    client = session or requests.Session()
    try:
        response = client.get(url, auth=auth, headers={"Range": "bytes=0-3"}, timeout=timeout, stream=True)
        head = response.raw.read(4, decode_content=False) if response.raw else b""
        status = response.status_code
        content_type = response.headers.get("Content-Type", "")
        response.close()
    except requests.RequestException as error:
        print(f"SEM ACESSO: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if head in ZIP_SIGNATURES:
        print(f"ACESSO OK: HTTP {status}, {content_type or 'sem tipo'}, o arquivo comeca como zip")
        return 0
    print(
        f"ACESSO RECUSADO: HTTP {status}, {content_type or 'sem tipo'}, os primeiros bytes nao sao de um zip",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.cloud.task", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Baixa, processa e publica a gold dos meses de CNPJ_MONTHS")
    check = sub.add_parser("probe", help="Confere o acesso ao servidor da Receita a partir deste ambiente")
    check.add_argument("--month", help="Mes AAAA-MM (padrao: o primeiro de CNPJ_MONTHS ou o mes corrente)")
    check.add_argument("--url-template", default=config.URL_TEMPLATE)
    check.add_argument("--share-token", default=None)
    check.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    environment = os.environ if env is None else env
    today = datetime.now(UTC).date()
    if args.command == "run":
        return run_task(environment, today=today)
    try:
        month = args.month or parse_months(environment.get(MONTHS_ENV), today)[0]
        if not MONTH_PATTERN.fullmatch(month):
            raise TaskConfigError(f"mes invalido: {month!r} (formato AAAA-MM)")
    except TaskConfigError as error:
        print(f"CONFIGURACAO INVALIDA: {error}", file=sys.stderr)
        return CONFIG_EXIT_CODE
    return probe(month, url_template=args.url_template, share_token=args.share_token, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
