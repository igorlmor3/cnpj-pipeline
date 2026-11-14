"""Demonstracao ponta a ponta com dados sinteticos.

Gera uma amostra sintetica, converte para Parquet na camada bronze, roda o profiling,
constroi a camada silver (tipagem, limpeza e quarentena), a camada gold (dimensoes, fato e
marts, com verificacoes) e roda as verificacoes de qualidade entre camadas. Tudo acontece em uma pasta
separada (padrao: data/sample) e nao toca em data/raw nem em docs/.

Exemplo:
    python -m cnpj_pipeline.demo
    python -m cnpj_pipeline.demo --rows 20000 --dir data/sample
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from cnpj_pipeline import bronze, config, gold, profiling, quality, sample, silver

log = logging.getLogger("cnpj_pipeline.demo")

# A amostra tem poucas linhas, entao uma linha quebrada pesa mais que nos dados reais.
DEMO_MAX_REJECT_RATIO = 0.01
# Pelo mesmo motivo, a amostra planta chaves invalidas e duplicadas que pesam mais que o normal.
DEMO_MAX_QUARANTINE_RATIO = 0.05


def run_demo(
    base_dir: Path,
    *,
    month: str = sample.DEFAULT_MONTH,
    rows: int = 3000,
    memory_limit: str = "1GB",
    threads: int | None = None,
) -> int:
    """Executa amostra, bronze, profiling, silver e gold dentro de base_dir. Retorna o codigo de saida."""
    if base_dir.resolve() in (config.DATA_DIR.resolve(), config.RAW_DIR.resolve()):
        log.error("Use uma pasta separada dos dados reais (por exemplo data/sample).")
        return 2

    raw_dir = base_dir / "raw"
    bronze_dir = base_dir / "bronze"
    report_dir = base_dir / "profiling"
    silver_dir = base_dir / "silver"
    gold_dir = base_dir / "gold"

    created = sample.generate(month, raw_dir, rows=rows)
    log.info("Amostra: %d arquivo(s) em %s", sum(len(v) for v in created.values()), raw_dir / month)

    code = bronze.run(
        month,
        list(config.ALL_TABLES),
        raw_dir=raw_dir,
        bronze_dir=bronze_dir,
        force=True,
        memory_limit=memory_limit,
        threads=threads,
        max_reject_ratio=DEMO_MAX_REJECT_RATIO,
        tmp_root=base_dir / "tmp",
    )
    if code:
        return code

    code = profiling.run(
        month,
        list(config.ALL_TABLES),
        bronze_dir=bronze_dir,
        out_dir=report_dir,
        memory_limit=memory_limit,
        threads=threads,
        sample_percent=100.0,
        skip_referential=False,
    )
    if code:
        return code

    code = silver.run(
        month,
        list(config.ALL_TABLES),
        bronze_dir=bronze_dir,
        silver_dir=silver_dir,
        report_dir=base_dir / "silver_report",
        force=True,
        memory_limit=memory_limit,
        threads=threads,
        max_quarantine_ratio=DEMO_MAX_QUARANTINE_RATIO,
        tmp_root=base_dir / "tmp",
    )
    if code:
        return code

    code = gold.run(
        month,
        None,
        silver_dir=silver_dir,
        gold_dir=gold_dir,
        report_dir=base_dir / "gold_report",
        force=True,
        memory_limit=memory_limit,
        threads=threads,
        tmp_root=base_dir / "tmp",
    )
    if code:
        return code

    code = quality.run(
        month,
        bronze_dir=bronze_dir,
        silver_dir=silver_dir,
        gold_dir=gold_dir,
        quality_dir=base_dir / "quality",
        report_dir=base_dir / "quality_report",
        quick=False,
        memory_limit=memory_limit,
        threads=threads,
        tmp_root=base_dir / "tmp",
    )
    if code == 0:
        log.info(
            "Demo concluida. Relatorios em %s, %s, %s e %s",
            report_dir / month,
            base_dir / "silver_report",
            base_dir / "gold_report",
            base_dir / "quality_report",
        )
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.demo",
        description="Roda amostra sintetica, bronze, profiling, silver e gold em uma pasta separada.",
    )
    parser.add_argument(
        "--dir", type=Path, default=config.DATA_DIR / "sample", help="Pasta da demo (padrao: data/sample)"
    )
    parser.add_argument(
        "--month", default=sample.DEFAULT_MONTH, help="Mes ficticio AAAA-MM (padrao: %(default)s)"
    )
    parser.add_argument("--rows", type=int, default=3000, help="Quantidade de empresas (padrao: %(default)s)")
    parser.add_argument(
        "--memory-limit", default="1GB", help="Limite de memoria do DuckDB (padrao: %(default)s)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.rows < 10:
        print("--rows deve ser >= 10", file=sys.stderr)
        return 2
    return run_demo(
        args.dir, month=args.month, rows=args.rows, memory_limit=args.memory_limit, threads=args.threads
    )


if __name__ == "__main__":
    sys.exit(main())
