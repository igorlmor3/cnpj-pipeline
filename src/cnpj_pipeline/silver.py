"""Camada silver: tipagem, limpeza e quarentena sobre a camada bronze.

Le data/bronze/<tabela>/reference_month=<mes>/*.parquet (tudo texto) e grava
data/silver/<tabela>/reference_month=<mes>/<tabela>.parquet com tipos corretos.

O que esta camada faz (regras provisorias em silver_rules.py):
- aplica a regra de cada coluna: datas viram DATE, decimais viram DECIMAL, S/N vira BOOLEAN,
  textos sao limpos, valores invalidos viram NULL (e sao contados);
- envia para a quarentena as linhas com chave obrigatoria invalida e as chaves duplicadas
  (mantem uma linha por chave, de forma deterministica);
- calcula a coluna derivada `cnpj` (14 posicoes) em Estabelecimentos;
- registra em manifesto e relatorio quantas linhas cada regra afetou.

A quarentena fica em data/silver/_quarantine/<tabela>/reference_month=<mes>/ com as colunas
originais da bronze e o motivo (_reject_reason). Se a fracao em quarentena de uma tabela
passar do limite, a tabela falha e a saida silver e removida.

Exemplos:
    python -m cnpj_pipeline.silver --month 2026-10
    python -m cnpj_pipeline.silver --month 2026-10 --tables empresas --force
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE, normalize_tables
from cnpj_pipeline.schemas import columns_for
from cnpj_pipeline.silver_rules import (
    DEDUP_KEYS,
    REQUIRED,
    RULES,
    TEXT_LIKE,
    clean_expr,
    output_type,
)

log = logging.getLogger("cnpj_pipeline.silver")

STATUS_CONVERTED = "converted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# Colunas de metadados que a bronze grava e a silver repassa.
LINEAGE = ("_source_file", "_reference_month", "_ingested_at")


@dataclass
class TableResult:
    table: str
    status: str
    rows_in: int = 0
    rows_out: int = 0
    invalid_key_rows: int = 0
    duplicate_rows_removed: int = 0
    seconds: float = 0.0
    parquet: str = ""
    parquet_bytes: int = 0
    columns: dict[str, dict] = field(default_factory=dict)
    error: str | None = None


# --------------------------------------------------------------------------- #
# Utilitarios
# --------------------------------------------------------------------------- #


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def bronze_glob(bronze_dir: Path, table: str, month: str) -> Path:
    return bronze_dir / table.lower() / f"reference_month={month}" / "*.parquet"


def silver_path(silver_dir: Path, table: str, month: str) -> Path:
    return silver_dir / table.lower() / f"reference_month={month}" / f"{table.lower()}.parquet"


def quarantine_path(silver_dir: Path, table: str, month: str) -> Path:
    return (
        silver_dir / "_quarantine" / table.lower() / f"reference_month={month}" / f"{table.lower()}.parquet"
    )


def _src(column: str) -> str:
    return f"s.{_ident(column)}"


def _clean(table: str, column: str) -> str:
    return clean_expr(RULES[table][column], _src(column))


# --------------------------------------------------------------------------- #
# Montagem das consultas
# --------------------------------------------------------------------------- #


def valid_condition(table: str) -> str:
    return " AND ".join(f"({_clean(table, c)}) IS NOT NULL" for c in REQUIRED[table])


def reject_reason_case(table: str) -> str:
    whens = " ".join(
        f"WHEN ({_clean(table, c)}) IS NULL THEN {_q('invalid_key:' + c)}" for c in REQUIRED[table]
    )
    return f"CASE {whens} END"


def rank_expression(table: str, has_duplicates: bool) -> str:
    """Posicao da linha dentro da sua chave.

    Fica a linha mais preenchida (mais colunas nao nulas depois da limpeza). Se houver empate, o
    desempate e deterministico, pelo hash do conteudo. So o hash escolhia, na pratica, ao acaso:
    em Empresas ele manteve um registro vazio e mandou o completo para a quarentena.
    """
    keys = DEDUP_KEYS.get(table)
    if not keys or not has_duplicates:
        return "1"
    partition = ", ".join(_clean(table, k) for k in keys)
    content = ", ".join(_src(c) for c in columns_for(table))
    filled = " + ".join(
        f"CASE WHEN ({_clean(table, c)}) IS NULL THEN 0 ELSE 1 END" for c in columns_for(table)
    )
    return (
        f"row_number() OVER (PARTITION BY {partition} "
        f"ORDER BY ({filled}) DESC, md5(concat_ws('|', {content})))"
    )


def select_list(table: str, processed_at: str) -> str:
    parts = [f"{_clean(table, c)} AS {_ident(c)}" for c in columns_for(table)]
    if table == "Estabelecimentos":
        pieces = " || ".join(_clean(table, c) for c in ("cnpj_basico", "cnpj_ordem", "cnpj_dv"))
        parts.append(f"{pieces} AS cnpj")
    parts += [f"s.{col}" for col in LINEAGE]
    parts.append(f"CAST({_q(processed_at)} AS TIMESTAMP) AS _silver_processed_at")
    return ",\n       ".join(parts)


def silver_query(table: str, processed_at: str, has_duplicates: bool) -> str:
    return f"""
        SELECT {select_list(table, processed_at)}
        FROM (
            SELECT s.*, {rank_expression(table, has_duplicates)} AS _rn
            FROM src s
            WHERE {valid_condition(table)}
        ) s
        WHERE s._rn = 1
    """


def quarantine_query(table: str, has_duplicates: bool) -> str:
    invalid = f"""
        SELECT s.*, {reject_reason_case(table)} AS _reject_reason
        FROM src s
        WHERE NOT ({valid_condition(table)})
    """
    if not has_duplicates:
        return invalid
    duplicates = f"""
        SELECT s.* EXCLUDE (_rn), 'duplicate_key' AS _reject_reason
        FROM (
            SELECT s.*, {rank_expression(table, True)} AS _rn
            FROM src s
            WHERE {valid_condition(table)}
        ) s
        WHERE s._rn > 1
    """
    return f"{invalid} UNION ALL {duplicates}"


def column_stats_query(table: str) -> tuple[str, list[tuple[str, str]]]:
    """Consulta que conta, por coluna, valores anulados e alterados pela regra."""
    aggregates = [
        "count(*) AS _total",
        f"count(*) FILTER (WHERE NOT ({valid_condition(table)})) AS _invalid_keys",
    ]
    labels: list[tuple[str, str]] = []
    for column in columns_for(table):
        kind = RULES[table][column]
        raw, clean = _src(column), _clean(table, column)
        nullified = _ident(column + "__nullified")
        aggregates.append(f"count(*) FILTER (WHERE {raw} IS NOT NULL AND ({clean}) IS NULL) AS {nullified}")
        labels.append((column, "nullified"))
        if kind in TEXT_LIKE:
            aggregates.append(
                f"count(*) FILTER (WHERE {raw} IS NOT NULL AND ({clean}) IS NOT NULL "
                f"AND CAST(({clean}) AS VARCHAR) <> {raw}) AS {_ident(column + '__changed')}"
            )
            labels.append((column, "changed"))
    return "SELECT " + ", ".join(aggregates) + " FROM src s", labels


def duplicates_query(table: str) -> str | None:
    keys = DEDUP_KEYS.get(table)
    if not keys:
        return None
    partition = ", ".join(_clean(table, k) for k in keys)
    return f"""
        SELECT coalesce(sum(n - 1), 0) FROM (
            SELECT count(*) AS n FROM src s WHERE {valid_condition(table)}
            GROUP BY {partition} HAVING count(*) > 1
        )
    """


def copy_to_parquet(con: duckdb.DuckDBPyConnection, query: str, path: Path) -> None:
    con.execute(f"COPY ({query}) TO {_q(path.as_posix())} (FORMAT parquet, COMPRESSION zstd)")


# --------------------------------------------------------------------------- #
# Processamento de uma tabela
# --------------------------------------------------------------------------- #


def process_table(
    con: duckdb.DuckDBPyConnection,
    table: str,
    month: str,
    bronze_dir: Path,
    silver_dir: Path,
    *,
    force: bool,
    max_quarantine_ratio: float,
) -> TableResult:
    out = silver_path(silver_dir, table, month)
    result = TableResult(
        table=table, status=STATUS_FAILED, parquet=str(out.relative_to(silver_dir).as_posix())
    )

    if out.exists() and not force:
        result.status = STATUS_SKIPPED
        result.rows_out = con.execute(f"SELECT count(*) FROM read_parquet({_q(out.as_posix())})").fetchone()[
            0
        ]
        result.parquet_bytes = out.stat().st_size
        log.info("%s: silver ja existe, pulando (use --force para refazer)", table)
        return result

    started = time.perf_counter()
    processed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    source = _q(bronze_glob(bronze_dir, table, month).as_posix())
    con.execute(
        f"CREATE OR REPLACE VIEW src AS SELECT * FROM read_parquet({source}, hive_partitioning=false)"
    )

    stats_sql, labels = column_stats_query(table)
    stats = con.execute(stats_sql).fetchone()
    result.rows_in, result.invalid_key_rows = stats[0], stats[1]
    columns: dict[str, dict] = {
        c: {"type": output_type(RULES[table][c]), "rule": RULES[table][c]} for c in columns_for(table)
    }
    for (column, metric), value in zip(labels, stats[2:], strict=True):
        columns[column][metric] = value
    result.columns = columns

    dup_sql = duplicates_query(table)
    result.duplicate_rows_removed = int(con.execute(dup_sql).fetchone()[0]) if dup_sql else 0
    has_duplicates = result.duplicate_rows_removed > 0

    quarantined = result.invalid_key_rows + result.duplicate_rows_removed
    ratio = quarantined / result.rows_in if result.rows_in else 0.0

    # Quarentena (gravada mesmo quando o limite e excedido, para inspecao)
    q_out = quarantine_path(silver_dir, table, month)
    q_out.parent.mkdir(parents=True, exist_ok=True)
    q_out.unlink(missing_ok=True)
    if quarantined:
        copy_to_parquet(con, quarantine_query(table, has_duplicates), q_out)

    if ratio > max_quarantine_ratio:
        result.error = (
            f"{quarantined} linha(s) em quarentena ({ratio:.4%}) acima do limite de "
            f"{max_quarantine_ratio:.4%}; detalhes em {q_out}"
        )
        log.error("%s: %s", table, result.error)
        return result

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    copy_to_parquet(con, silver_query(table, processed_at, has_duplicates), tmp)
    tmp.replace(out)

    result.rows_out = con.execute(f"SELECT count(*) FROM read_parquet({_q(out.as_posix())})").fetchone()[0]
    expected = result.rows_in - quarantined
    if result.rows_out != expected:
        out.unlink(missing_ok=True)
        result.error = f"contagem inconsistente: esperado {expected} linhas, gravado {result.rows_out}"
        log.error("%s: %s", table, result.error)
        return result

    result.parquet_bytes = out.stat().st_size
    result.seconds = round(time.perf_counter() - started, 2)
    result.status = STATUS_CONVERTED
    log.info(
        "%s: %d -> %d linhas (%d chave(s) invalida(s), %d duplicada(s)) em %.1fs",
        table,
        result.rows_in,
        result.rows_out,
        result.invalid_key_rows,
        result.duplicate_rows_removed,
        result.seconds,
    )
    return result


# --------------------------------------------------------------------------- #
# Manifesto, relatorio e orquestracao
# --------------------------------------------------------------------------- #


def write_manifest(silver_dir: Path, month: str, results: list[TableResult]) -> Path:
    path = silver_dir / f"_manifest_{month}.json"
    tables: dict[str, dict] = {}
    if path.exists():
        tables = json.loads(path.read_text(encoding="utf-8")).get("tables", {})
    for r in results:
        if r.status == STATUS_CONVERTED:
            entry = asdict(r)
            entry.pop("error", None)
            tables[r.table] = entry
    manifest = {
        "reference_month": month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rules_status": "provisorias: confirmar contra o profiling do mes real",
        "tables": dict(sorted(tables.items())),
    }
    silver_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_report(month: str, tables: dict[str, dict]) -> str:
    lines = [
        f"# Camada silver: regras aplicadas ({month})",
        "",
        "As regras são provisórias e devem ser confirmadas contra o profiling do mês real.",
        "Este relatório contém apenas contagens.",
        "",
        "## Resumo por tabela",
        "",
        "| Tabela | Linhas de entrada | Linhas de saída | Chaves inválidas | Duplicadas removidas |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, t in tables.items():
        cells = [name, t["rows_in"], t["rows_out"], t["invalid_key_rows"], t["duplicate_rows_removed"]]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    for name, t in tables.items():
        affected = {c: v for c, v in t["columns"].items() if v.get("nullified") or v.get("changed")}
        lines += ["", f"## {name}", ""]
        if not affected:
            lines.append("Nenhum valor foi anulado ou alterado pelas regras.")
            continue
        lines += [
            "| Coluna | Regra | Tipo | Valores anulados | Valores alterados |",
            "| --- | --- | --- | ---: | ---: |",
        ]
        for column, v in affected.items():
            cells = [f"`{column}`", v["rule"], v["type"], v.get("nullified", 0), v.get("changed", "-")]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def run(
    month: str,
    tables: list[str],
    *,
    bronze_dir: Path,
    silver_dir: Path,
    report_dir: Path | None,
    force: bool,
    memory_limit: str,
    threads: int | None,
    max_quarantine_ratio: float,
    tmp_root: Path,
) -> int:
    present = [t for t in tables if any(bronze_glob(bronze_dir, t, month).parent.glob("*.parquet"))]
    for table in tables:
        if table not in present:
            log.warning("Tabela %s: sem Parquet na bronze para %s, pulando", table, month)
    if not present:
        log.error("Nada para processar. Rode antes: python -m cnpj_pipeline.bronze --month %s", month)
        return 1

    tmp_root.mkdir(parents=True, exist_ok=True)
    options: dict[str, str | int] = {
        "memory_limit": memory_limit,
        "temp_directory": str(tmp_root / "duckdb_spill"),
    }
    if threads:
        options["threads"] = threads
    con = duckdb.connect(config=options)

    results: list[TableResult] = []
    try:
        for table in present:
            try:
                results.append(
                    process_table(
                        con,
                        table,
                        month,
                        bronze_dir,
                        silver_dir,
                        force=force,
                        max_quarantine_ratio=max_quarantine_ratio,
                    )
                )
            except duckdb.Error as exc:
                log.error("%s: erro do DuckDB: %s", table, exc)
                results.append(TableResult(table=table, status=STATUS_FAILED, error=str(exc)))
    finally:
        con.close()
        shutil.rmtree(tmp_root / "duckdb_spill", ignore_errors=True)

    manifest_path = write_manifest(silver_dir, month, results)
    if report_dir is not None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{month}.md").write_text(render_report(month, manifest["tables"]), encoding="utf-8")

    failed = [r for r in results if r.status == STATUS_FAILED]
    log.info(
        "Resumo: %d convertida(s), %d pulada(s), %d com falha. Manifesto: %s",
        sum(r.status == STATUS_CONVERTED for r in results),
        sum(r.status == STATUS_SKIPPED for r in results),
        len(failed),
        manifest_path,
    )
    for r in failed:
        log.error("%s: %s", r.table, r.error)
    return 1 if failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.silver",
        description="Converte a camada bronze em silver: tipos corretos, limpeza e quarentena.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument(
        "--tables",
        nargs="+",
        metavar="TABELA",
        help=f"Tabelas (padrao: todas com Parquet na bronze). Validas: {', '.join(config.ALL_TABLES)}",
    )
    parser.add_argument("--bronze-dir", type=Path, default=config.BRONZE_DIR)
    parser.add_argument("--silver-dir", type=Path, default=config.SILVER_DIR)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=config.PROJECT_ROOT / "docs" / "silver",
        help="Pasta do relatorio de regras aplicadas (padrao: docs/silver)",
    )
    parser.add_argument("--force", action="store_true", help="Refaz tabelas que ja existem na silver")
    parser.add_argument(
        "--memory-limit", default="4GB", help="Limite de memoria do DuckDB (padrao: %(default)s)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    parser.add_argument(
        "--max-quarantine-ratio",
        type=float,
        default=0.01,
        help="Fracao maxima de linhas em quarentena por tabela antes de falhar (padrao: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detalhados")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM, por exemplo 2026-10.", file=sys.stderr)
        return 2
    if not 0 <= args.max_quarantine_ratio <= 1:
        print("--max-quarantine-ratio deve estar entre 0 e 1", file=sys.stderr)
        return 2
    try:
        tables = normalize_tables(args.tables)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    return run(
        args.month,
        tables,
        bronze_dir=args.bronze_dir,
        silver_dir=args.silver_dir,
        report_dir=args.report_dir,
        force=args.force,
        memory_limit=args.memory_limit,
        threads=args.threads,
        max_quarantine_ratio=args.max_quarantine_ratio,
        tmp_root=config.DATA_DIR / "tmp",
    )


if __name__ == "__main__":
    sys.exit(main())
