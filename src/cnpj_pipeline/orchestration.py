"""Orquestracao do pipeline: o que cada tarefa da DAG executa.

A DAG do Airflow (orchestration/dags/cnpj_monthly.py) e fina de proposito: cada tarefa chama
`run_stage`, que monta a linha de comando do modulo correspondente e a executa em um processo
separado (o mesmo comando que se roda na mao, o que isola a memoria de cada etapa). Toda a logica
de caminhos, modos e opcoes fica aqui, em Python puro, para ser testada sem o Airflow.

O mesmo fluxo roda sem Airflow, direto no terminal, para um ou mais meses em sequencia (e e o
caminho mais rapido para dados reais, sem passar pelo disco virtual do Docker):

    python -m cnpj_pipeline.orchestration --mode full --months 2026-07 2026-08 2026-09

Dois modos:
- sample: dados sinteticos em data/sample, com os limites de tolerancia da demonstracao. Serve para
  ver a DAG funcionando em segundos e nunca toca em data/raw nem em docs/.
- full: o mes real, em data/, com os relatorios em docs/. A publicacao na AWS so acontece com
  publish=true.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cnpj_pipeline import config

log = logging.getLogger(__name__)

MODE_SAMPLE = "sample"
MODE_FULL = "full"
MODES = (MODE_SAMPLE, MODE_FULL)

# Mes ficticio da amostra (o mesmo de cnpj_pipeline.sample.DEFAULT_MONTH).
SAMPLE_MONTH = "2000-01"
# Tolerancias e memoria da demonstracao (os mesmos valores de cnpj_pipeline.demo): a amostra tem
# poucas linhas, entao uma linha quebrada ou duplicada pesa mais que nos dados reais.
SAMPLE_MAX_REJECT_RATIO = "0.01"
SAMPLE_MAX_QUARANTINE_RATIO = "0.05"
SAMPLE_MEMORY_LIMIT = "1GB"

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Ids das tarefas da DAG, na ordem do fluxo.
SOURCE_STAGES = {MODE_FULL: "baixar_dados", MODE_SAMPLE: "gerar_amostra"}
STAGES = (
    "baixar_dados",
    "gerar_amostra",
    "bronze",
    "silver",
    "gold",
    "qualidade",
    "exportar_site",
    "publicar_s3",
    "tabelas_athena",
)
CLOUD_STAGES = ("publicar_s3", "tabelas_athena")


class OrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunConfig:
    """Parametros de uma execucao da DAG."""

    mode: str = MODE_SAMPLE
    month: str = "2026-10"
    force: bool = False
    publish: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise OrchestrationError(f"modo invalido: {self.mode!r} (use {' ou '.join(MODES)})")
        if not MONTH_PATTERN.fullmatch(self.month):
            raise OrchestrationError(f"mes invalido: {self.month!r} (formato AAAA-MM)")

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> RunConfig:
        return cls(
            mode=str(params.get("mode", MODE_SAMPLE)),
            month=str(params.get("month", cls.month)),
            force=_as_bool(params.get("force", False)),
            publish=_as_bool(params.get("publish", False)),
        )

    @property
    def effective_month(self) -> str:
        """Na amostra o mes e sempre o ficticio; no modo completo e o escolhido."""
        return SAMPLE_MONTH if self.mode == MODE_SAMPLE else self.month

    @property
    def effective_force(self) -> bool:
        """A amostra e refeita a cada execucao; no modo completo, so com force."""
        return True if self.mode == MODE_SAMPLE else self.force


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in ("1", "true", "sim", "yes")
    return bool(value)


@dataclass(frozen=True)
class Layout:
    """Onde cada etapa le e grava, conforme o modo."""

    base: Path
    silver_report: Path
    gold_report: Path
    quality_report: Path
    site_out: Path

    @property
    def raw(self) -> Path:
        return self.base / "raw"

    @property
    def bronze(self) -> Path:
        return self.base / "bronze"

    @property
    def silver(self) -> Path:
        return self.base / "silver"

    @property
    def gold(self) -> Path:
        return self.base / "gold"

    @property
    def quality(self) -> Path:
        return self.base / "quality"


def layout(cfg: RunConfig) -> Layout:
    if cfg.mode == MODE_SAMPLE:
        base = config.DATA_DIR / "sample"
        return Layout(
            base=base,
            silver_report=base / "silver_report",
            gold_report=base / "gold_report",
            quality_report=base / "quality_report",
            site_out=base / "site_data",
        )
    docs = config.PROJECT_ROOT / "docs"
    return Layout(
        base=config.DATA_DIR,
        silver_report=docs / "silver",
        gold_report=docs / "gold",
        quality_report=docs / "quality",
        site_out=config.PROJECT_ROOT / "site" / "public" / "data",
    )


def choose_source(params: Mapping[str, Any]) -> str:
    """Id da tarefa que obtem os dados brutos: baixar (modo completo) ou gerar a amostra."""
    return SOURCE_STAGES[RunConfig.from_params(params).mode]


def should_publish(params: Mapping[str, Any]) -> bool:
    """Publicar na AWS so no modo completo e quando pedido; a amostra nunca vai para a nuvem."""
    cfg = RunConfig.from_params(params)
    return cfg.mode == MODE_FULL and cfg.publish


def build_argv(stage: str, cfg: RunConfig) -> list[str]:
    """Comando (lista de argumentos) da etapa, pronto para o subprocess."""
    if stage not in STAGES:
        raise OrchestrationError(f"etapa desconhecida: {stage!r}")
    if stage == "baixar_dados" and cfg.mode != MODE_FULL:
        raise OrchestrationError("baixar_dados so existe no modo full")
    if stage == "gerar_amostra" and cfg.mode != MODE_SAMPLE:
        raise OrchestrationError("gerar_amostra so existe no modo sample")
    if stage in CLOUD_STAGES and cfg.mode != MODE_FULL:
        raise OrchestrationError(f"{stage} so existe no modo full")

    lay = layout(cfg)
    month = cfg.effective_month
    force = ["--force"] if cfg.effective_force else []
    sample = cfg.mode == MODE_SAMPLE
    memory = ["--memory-limit", SAMPLE_MEMORY_LIMIT] if sample else []

    def run(module: str, *args: str) -> list[str]:
        return [sys.executable, "-m", f"cnpj_pipeline.{module}", *args]

    if stage == "baixar_dados":
        return run("download", "--month", month, "--out-dir", str(lay.raw))
    if stage == "gerar_amostra":
        return run("sample", "--month", month, "--out-dir", str(lay.raw))
    if stage == "bronze":
        tolerance = ["--max-reject-ratio", SAMPLE_MAX_REJECT_RATIO] if sample else []
        return run(
            "bronze", "--month", month, "--raw-dir", str(lay.raw), "--bronze-dir", str(lay.bronze),
            *force, *tolerance, *memory,
        )  # fmt: skip
    if stage == "silver":
        tolerance = ["--max-quarantine-ratio", SAMPLE_MAX_QUARANTINE_RATIO] if sample else []
        return run(
            "silver", "--month", month, "--bronze-dir", str(lay.bronze), "--silver-dir", str(lay.silver),
            "--report-dir", str(lay.silver_report), *force, *tolerance, *memory,
        )  # fmt: skip
    if stage == "gold":
        return run(
            "gold", "--month", month, "--silver-dir", str(lay.silver), "--gold-dir", str(lay.gold),
            "--report-dir", str(lay.gold_report), *force, *memory,
        )  # fmt: skip
    if stage == "qualidade":
        return run(
            "quality", "--month", month, "--bronze-dir", str(lay.bronze), "--silver-dir", str(lay.silver),
            "--gold-dir", str(lay.gold), "--quality-dir", str(lay.quality),
            "--report-dir", str(lay.quality_report), *memory,
        )  # fmt: skip
    if stage == "exportar_site":
        return run("site_export", "--month", month, "--gold-dir", str(lay.gold), "--out", str(lay.site_out))
    if stage == "publicar_s3":
        return run("cloud.publish", "--month", month, "--layers", "gold", "--data-dir", str(config.DATA_DIR))
    return run("cloud.athena", "create-tables", "--layer", "gold", "--month", month)


def run_stage(stage: str, params: Mapping[str, Any]) -> None:
    """Executa uma etapa. Falha (excecao) se o comando sair com codigo diferente de zero, e e isso que
    faz o Airflow marcar a tarefa como falha e nao executar as etapas seguintes."""
    cfg = RunConfig.from_params(params)
    argv = build_argv(stage, cfg)
    log.info("Etapa %s (modo %s, mes %s): %s", stage, cfg.mode, cfg.effective_month, " ".join(argv))
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace"
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            log.info("%s", line.rstrip())
    if process.returncode != 0:
        raise OrchestrationError(f"a etapa {stage} terminou com codigo {process.returncode}")


class FlowFailed(OrchestrationError):
    """Uma etapa falhou: diz em que mes e etapa e o que ja tinha terminado antes."""

    def __init__(self, month: str, stage: str, cause: Exception, completed: list[StageResult]) -> None:
        super().__init__(f"{month}, etapa {stage}: {cause}")
        self.month = month
        self.stage = stage
        self.completed = completed


@dataclass(frozen=True)
class StageResult:
    month: str
    stage: str
    seconds: float


def flow(cfg: RunConfig) -> list[str]:
    """Etapas de uma execucao completa de um mes, na ordem. A nuvem so entra com publish no modo full."""
    stages = [SOURCE_STAGES[cfg.mode], "bronze", "silver", "gold", "qualidade", "exportar_site"]
    if cfg.mode == MODE_FULL and cfg.publish:
        stages.extend(CLOUD_STAGES)
    return stages


def run_flow(
    months: Sequence[str],
    *,
    mode: str = MODE_FULL,
    force: bool = False,
    publish: bool = False,
    from_stage: str | None = None,
    runner: Callable[[str, Mapping[str, Any]], None] = run_stage,
    clock: Callable[[], float] = time.monotonic,
) -> list[StageResult]:
    """Roda o fluxo de cada mes, um apos o outro. Para na primeira falha (os meses seguintes dependem
    de a maquina ter espaco e de o mes anterior estar bom). `from_stage` retoma o primeiro mes a partir
    de uma etapa; os demais meses rodam inteiros. Todos os parametros sao validados antes de comecar."""
    if not months:
        raise OrchestrationError("informe pelo menos um mes")
    if len(set(months)) != len(months):
        raise OrchestrationError("mes repetido na lista")
    configs = [RunConfig(mode=mode, month=month, force=force, publish=publish) for month in months]
    if from_stage is not None and from_stage not in flow(configs[0]):
        raise OrchestrationError(
            f"etapa {from_stage!r} nao faz parte do fluxo: {', '.join(flow(configs[0]))}"
        )

    results: list[StageResult] = []
    for index, cfg in enumerate(configs):
        stages = flow(cfg)
        if index == 0 and from_stage is not None:
            stages = stages[stages.index(from_stage) :]
        params = {"mode": cfg.mode, "month": cfg.month, "force": cfg.force, "publish": cfg.publish}
        for stage in stages:
            started = clock()
            try:
                runner(stage, params)
            except Exception as error:
                raise FlowFailed(cfg.effective_month, stage, error, results) from error
            result = StageResult(cfg.effective_month, stage, clock() - started)
            results.append(result)
            log.info(
                "Etapa %s do mes %s concluida em %s",
                stage,
                cfg.effective_month,
                format_duration(result.seconds),
            )
    return results


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def summary(results: Sequence[StageResult]) -> str:
    lines = ["Resumo (mes, etapa, duracao):"]
    lines += [f"  {r.month}  {r.stage:<14} {format_duration(r.seconds)}" for r in results]
    for month in dict.fromkeys(r.month for r in results):
        total = sum(r.seconds for r in results if r.month == month)
        lines.append(f"  {month}  total          {format_duration(total)}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.orchestration",
        description="Roda o fluxo completo (dados brutos, bronze, silver, gold, qualidade, exportacao para o "
        "site) para um ou mais meses, em sequencia, sem o Airflow.",
    )
    parser.add_argument(
        "--months", nargs="+", required=True, metavar="AAAA-MM", help="Meses, na ordem de execucao"
    )
    parser.add_argument("--mode", choices=MODES, default=MODE_FULL, help="full (padrao) ou sample")
    parser.add_argument("--force", action="store_true", help="Refaz bronze, silver e gold que ja existem")
    parser.add_argument("--publish", action="store_true", help="No modo full, publica a gold na AWS ao final")
    parser.add_argument(
        "--from-stage",
        choices=STAGES,
        help="Retoma o primeiro mes a partir desta etapa (ex.: gold, depois de uma falha)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout
    )
    try:
        results = run_flow(
            args.months, mode=args.mode, force=args.force, publish=args.publish, from_stage=args.from_stage
        )
    except FlowFailed as failure:
        if failure.completed:
            print(summary(failure.completed))
        print(f"FALHOU: {failure}", file=sys.stderr)
        remaining = (
            args.months[args.months.index(failure.month) :] if failure.month in args.months else args.months
        )
        print(
            f"Para retomar: python -m cnpj_pipeline.orchestration --mode {args.mode} "
            f"--months {' '.join(remaining)} --from-stage {failure.stage}",
            file=sys.stderr,
        )
        return 1
    except OrchestrationError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    print(summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
