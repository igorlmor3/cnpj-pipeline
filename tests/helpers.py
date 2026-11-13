"""Utilitarios compartilhados pelos testes."""

from __future__ import annotations

from pathlib import Path

import duckdb

from cnpj_pipeline.schemas import columns_for

MONTH = "2026-10"
LINEAGE_COLUMNS = ("_source_file", "_reference_month", "_ingested_at")


def write_bronze(bronze: Path, table: str, rows: list[dict], name: str | None = None) -> Path:
    """Cria um Parquet de teste na estrutura da bronze. Colunas ausentes ficam NULL.

    Alem das colunas da tabela, grava as colunas de linhagem que a bronze real grava.
    """
    columns = list(columns_for(table)) + list(LINEAGE_COLUMNS)
    out = bronze / table.lower() / f"reference_month={MONTH}" / f"{name or table + '0'}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (" + ", ".join(f'"{c}" VARCHAR' for c in columns) + ")")
    placeholders = ", ".join("?" for _ in columns)
    defaults = {
        "_source_file": f"{table}0.zip",
        "_reference_month": MONTH,
        "_ingested_at": "2026-11-01 00:00:00",
    }
    con.executemany(
        f"INSERT INTO t VALUES ({placeholders})",
        [[row.get(c, defaults.get(c)) for c in columns] for row in rows],
    )
    con.execute(f"COPY t TO '{out.as_posix()}' (FORMAT parquet)")
    con.close()
    return out
