"""Monta a entrada do experimento: da silver local, so as colunas que o SQL da gold usa.

A silver completa tem nomes de socios, enderecos e contatos, e o projeto a mantem fora da nuvem. O fato
e os marts usam um punhado de colunas, e e so isso que este script grava, no mesmo layout da silver:

    python experiments/glue_spark/make_input.py --month 2026-10 --out-dir data/experiments/spark_input

Depois, envie a pasta para o S3 (veja README.md desta pasta).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb
from spark_gold import INPUT_COLUMNS

try:
    from cnpj_pipeline import config
except ImportError:  # fora do ambiente do projeto
    config = None


def build_input(silver_dir: Path, out_dir: Path, month: str) -> dict[str, int]:
    """Grava a fatia de cada tabela e devolve as linhas gravadas por tabela."""
    rows: dict[str, int] = {}
    con = duckdb.connect()
    try:
        for table, columns in INPUT_COLUMNS.items():
            source = silver_dir / table.lower() / f"reference_month={month}" / f"{table.lower()}.parquet"
            if not source.exists():
                raise FileNotFoundError(f"silver de {table} para {month} nao encontrada: {source}")
            target_dir = out_dir / table.lower() / f"reference_month={month}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{table.lower()}.parquet"
            selected = ", ".join(columns)
            con.execute(
                f"COPY (SELECT {selected} FROM read_parquet(?)) TO ? (FORMAT parquet, COMPRESSION zstd)",
                [source.as_posix(), target.as_posix()],
            )
            rows[table] = con.execute("SELECT count(*) FROM read_parquet(?)", [target.as_posix()]).fetchone()[
                0
            ]
    finally:
        con.close()
    return rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_silver = config.SILVER_DIR if config else Path("data/silver")
    parser = argparse.ArgumentParser(description="Entrada do experimento Glue/Spark")
    parser.add_argument("--month", required=True, help="Mes de referencia AAAA-MM")
    parser.add_argument("--silver-dir", type=Path, default=default_silver)
    parser.add_argument("--out-dir", type=Path, default=Path("data/experiments/spark_input"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows = build_input(args.silver_dir, args.out_dir, args.month)
    except FileNotFoundError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2
    for table, count in rows.items():
        print(f"{table}: {count} linhas")
    size = sum(p.stat().st_size for p in args.out_dir.rglob("*.parquet"))
    print(f"Gravado em {args.out_dir} ({size / 1024**2:.0f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
