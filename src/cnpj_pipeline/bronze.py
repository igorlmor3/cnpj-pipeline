"""Camada bronze: converte os CSVs dos arquivos zip da Receita em Parquet.

Regras desta camada:
- nenhuma regra de negocio: todas as colunas sao texto, sem conversao de tipos;
- campos vazios viram NULL (o CSV nao distingue vazio de ausente);
- o encoding ISO-8859-1 (latin-1) e convertido para UTF-8;
- linhas com quantidade de colunas errada ou aspas invalidas nao sao perdidas em
  silencio: vao para data/bronze/_rejects/ com o numero da linha e o motivo;
- colunas de metadados sao adicionadas: _source_file, _reference_month, _ingested_at;
- se o numero de colunas do arquivo nao bate com schemas.py, o processamento para.

Saida: data/bronze/<tabela>/reference_month=<AAAA-MM>/<arquivo>.parquet

Exemplos:
    python -m cnpj_pipeline.bronze --month 2026-10 --tables cnaes motivos
    python -m cnpj_pipeline.bronze --month 2026-10 --memory-limit 6GB --threads 4
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE, normalize_tables
from cnpj_pipeline.schemas import columns_for

log = logging.getLogger("cnpj_pipeline.bronze")

CSV_ENCODING = "latin-1"
CSV_DELIMITER = ";"
CSV_QUOTE = '"'

STATUS_CONVERTED = "converted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


class SchemaMismatchError(Exception):
    """O numero de colunas do arquivo difere do esquema esperado."""


@dataclass
class FileResult:
    table: str
    source_zip: str
    member: str
    parquet: str
    status: str
    rows: int = 0
    rejected_lines: int = 0
    csv_bytes: int = 0
    parquet_bytes: int = 0
    seconds: float = 0.0
    error: str | None = None


# --------------------------------------------------------------------------- #
# Localizacao dos arquivos
# --------------------------------------------------------------------------- #


def find_zip_files(month_dir: Path, table: str) -> list[Path]:
    """Zips de uma tabela na pasta do mes, em ordem numerica (Empresas0, Empresas1, ...)."""
    pattern = re.compile(rf"^{re.escape(table)}(\d*)\.zip$", re.IGNORECASE)
    found = []
    for path in month_dir.iterdir() if month_dir.is_dir() else []:
        match = pattern.match(path.name)
        if match and path.is_file():
            found.append((int(match.group(1) or -1), path))
    return [p for _, p in sorted(found)]


def parquet_path(bronze_dir: Path, table: str, month: str, stem: str) -> Path:
    return bronze_dir / table.lower() / f"reference_month={month}" / f"{stem}.parquet"


# --------------------------------------------------------------------------- #
# Validacao e conversao
# --------------------------------------------------------------------------- #


def check_layout(csv_path: Path, expected_columns: tuple[str, ...]) -> None:
    """Confere a quantidade de colunas da primeira linha contra o esquema."""
    with csv_path.open(encoding=CSV_ENCODING, newline="") as fh:
        first = next(csv.reader(fh, delimiter=CSV_DELIMITER, quotechar=CSV_QUOTE), None)
    if first is None:
        raise SchemaMismatchError(f"{csv_path.name}: arquivo vazio")
    if len(first) != len(expected_columns):
        raise SchemaMismatchError(
            f"{csv_path.name}: o arquivo tem {len(first)} colunas, mas schemas.py define "
            f"{len(expected_columns)}. O layout pode ter mudado; confira o dicionario do mes."
        )


def _q(value: str) -> str:
    """Escapa um texto para uso como literal SQL."""
    return "'" + value.replace("'", "''") + "'"


def convert_csv(
    con: duckdb.DuckDBPyConnection,
    csv_path: Path,
    out_path: Path,
    columns: tuple[str, ...],
    *,
    source_file: str,
    reference_month: str,
    ingested_at: str,
    rejects_path: Path,
) -> tuple[int, int]:
    """Converte um CSV em Parquet. Retorna (linhas gravadas, linhas rejeitadas)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_name(out_path.name + ".tmp")
    tmp_out.unlink(missing_ok=True)

    column_types = ", ".join(f"{_q(c)}: 'VARCHAR'" for c in columns)
    con.execute(
        f"""
        COPY (
            SELECT *,
                   {_q(source_file)} AS _source_file,
                   {_q(reference_month)} AS _reference_month,
                   CAST({_q(ingested_at)} AS TIMESTAMP) AS _ingested_at
            FROM read_csv(
                {_q(str(csv_path))},
                delim={_q(CSV_DELIMITER)},
                quote={_q(CSV_QUOTE)},
                escape={_q(CSV_QUOTE)},
                header=false,
                columns={{{column_types}}},
                encoding={_q(CSV_ENCODING)},
                ignore_errors=true,
                store_rejects=true
            )
        ) TO {_q(str(tmp_out))} (FORMAT parquet, COMPRESSION zstd)
        """
    )
    tmp_out.replace(out_path)

    rows = con.execute(f"SELECT count(*) FROM read_parquet({_q(str(out_path))})").fetchone()[0]
    rejected = con.execute("SELECT count(DISTINCT line) FROM reject_errors").fetchone()[0]

    if rejected:
        rejects_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            COPY (
                SELECT {_q(source_file)} AS _source_file, line, column_idx, column_name,
                       CAST(error_type AS VARCHAR) AS error_type, csv_line, error_message
                FROM reject_errors
                ORDER BY line, column_idx
            ) TO {_q(str(rejects_path))} (FORMAT parquet, COMPRESSION zstd)
            """
        )
    return rows, rejected


def _extract_members(zip_path: Path, tmp_dir: Path) -> list[tuple[str, Path]]:
    """Extrai os arquivos do zip em streaming (o DuckDB nao le CSV dentro de zip)."""
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        for index, member in enumerate(members):
            target = tmp_dir / f"{zip_path.stem}_{index}.csv"
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            extracted.append((member.filename, target))
    return extracted


def process_zip(
    zip_path: Path,
    table: str,
    month: str,
    bronze_dir: Path,
    *,
    force: bool,
    memory_limit: str,
    threads: int | None,
    max_reject_ratio: float,
    tmp_root: Path,
) -> list[FileResult]:
    """Converte todos os CSVs de um zip. Um Parquet por CSV."""
    columns = columns_for(table)
    results: list[FileResult] = []
    tmp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=tmp_root, prefix="bronze_") as tmp:
        tmp_dir = Path(tmp)
        try:
            members = _extract_members(zip_path, tmp_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            return [FileResult(table, zip_path.name, "", "", STATUS_FAILED, error=f"zip invalido: {exc}")]

        for index, (member_name, csv_path) in enumerate(members):
            stem = zip_path.stem if len(members) == 1 else f"{zip_path.stem}_{index}"
            out_path = parquet_path(bronze_dir, table, month, stem)
            rejects_path = (
                bronze_dir / "_rejects" / table.lower() / f"reference_month={month}" / f"{stem}.parquet"
            )
            result = FileResult(
                table=table,
                source_zip=zip_path.name,
                member=member_name,
                parquet=out_path.relative_to(bronze_dir).as_posix(),
                status=STATUS_FAILED,
                csv_bytes=csv_path.stat().st_size,
            )

            if out_path.exists() and not force:
                con = duckdb.connect()
                result.rows = con.execute(
                    f"SELECT count(*) FROM read_parquet({_q(str(out_path))})"
                ).fetchone()[0]
                con.close()
                result.parquet_bytes = out_path.stat().st_size
                result.status = STATUS_SKIPPED
                log.info("%s: parquet ja existe, pulando (use --force para refazer)", stem)
                results.append(result)
                continue

            started = time.perf_counter()
            try:
                check_layout(csv_path, columns)
                config_options: dict[str, str | int] = {
                    "memory_limit": memory_limit,
                    "temp_directory": str(tmp_dir / "duckdb_spill"),
                }
                if threads:
                    config_options["threads"] = threads
                con = duckdb.connect(config=config_options)
                try:
                    rows, rejected = convert_csv(
                        con,
                        csv_path,
                        out_path,
                        columns,
                        source_file=zip_path.name,
                        reference_month=month,
                        ingested_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                        rejects_path=rejects_path,
                    )
                finally:
                    con.close()
            except SchemaMismatchError as exc:
                result.error = str(exc)
                log.error("%s", exc)
                results.append(result)
                continue
            except duckdb.Error as exc:
                out_path.with_name(out_path.name + ".tmp").unlink(missing_ok=True)
                result.error = f"erro do DuckDB: {exc}"
                log.error("%s: %s", stem, result.error)
                results.append(result)
                continue

            result.rows = rows
            result.rejected_lines = rejected
            result.seconds = round(time.perf_counter() - started, 2)
            result.parquet_bytes = out_path.stat().st_size

            ratio = rejected / (rows + rejected) if (rows + rejected) else 0.0
            if ratio > max_reject_ratio:
                out_path.unlink(missing_ok=True)
                result.error = (
                    f"{rejected} linhas rejeitadas ({ratio:.4%}) acima do limite de "
                    f"{max_reject_ratio:.4%}; detalhes em {rejects_path}"
                )
                log.error("%s: %s", stem, result.error)
            else:
                result.status = STATUS_CONVERTED
                if rejected:
                    log.warning("%s: %d linha(s) rejeitada(s), ver %s", stem, rejected, rejects_path)
                log.info(
                    "%s: %d linhas, %.1f MiB csv -> %.1f MiB parquet em %.1fs",
                    stem,
                    rows,
                    result.csv_bytes / 2**20,
                    result.parquet_bytes / 2**20,
                    result.seconds,
                )
            results.append(result)
    return results


# --------------------------------------------------------------------------- #
# Orquestracao e manifesto
# --------------------------------------------------------------------------- #


def write_manifest(bronze_dir: Path, month: str, results: list[FileResult]) -> Path:
    """Grava (ou atualiza) data/bronze/_manifest_<mes>.json com os arquivos processados."""
    path = bronze_dir / f"_manifest_{month}.json"
    entries: dict[str, dict] = {}
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        entries = {e["parquet"]: e for e in previous.get("files", [])}

    for r in results:
        if r.status in (STATUS_CONVERTED, STATUS_SKIPPED):
            entry = asdict(r)
            entry.pop("error", None)
            if r.status == STATUS_SKIPPED and r.parquet in entries:
                # Preserva metricas da execucao original (tempo, rejeitadas)
                entry = {**entries[r.parquet], "status": STATUS_SKIPPED}
            entries[r.parquet] = entry

    files = sorted(entries.values(), key=lambda e: e["parquet"])
    manifest = {
        "reference_month": month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_rows": sum(e["rows"] for e in files),
        "total_rejected_lines": sum(e["rejected_lines"] for e in files),
        "total_csv_bytes": sum(e["csv_bytes"] for e in files),
        "total_parquet_bytes": sum(e["parquet_bytes"] for e in files),
        "files": files,
    }
    bronze_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run(
    month: str,
    tables: list[str],
    *,
    raw_dir: Path,
    bronze_dir: Path,
    force: bool,
    memory_limit: str,
    threads: int | None,
    max_reject_ratio: float,
    tmp_root: Path,
) -> int:
    month_dir = raw_dir / month
    all_results: list[FileResult] = []
    tables_without_files = []

    for table in tables:
        zips = find_zip_files(month_dir, table)
        if not zips:
            tables_without_files.append(table)
            log.warning("Tabela %s: nenhum zip encontrado em %s", table, month_dir)
            continue
        log.info("Tabela %s: %d arquivo(s)", table, len(zips))
        for zip_path in zips:
            all_results.extend(
                process_zip(
                    zip_path,
                    table,
                    month,
                    bronze_dir,
                    force=force,
                    memory_limit=memory_limit,
                    threads=threads,
                    max_reject_ratio=max_reject_ratio,
                    tmp_root=tmp_root,
                )
            )

    if not all_results:
        log.error("Nenhum arquivo processado. Confira se os zips estao em %s", month_dir)
        return 1

    manifest_path = write_manifest(bronze_dir, month, all_results)
    failed = [r for r in all_results if r.status == STATUS_FAILED]
    converted = sum(r.status == STATUS_CONVERTED for r in all_results)
    skipped = sum(r.status == STATUS_SKIPPED for r in all_results)
    log.info(
        "Resumo: %d convertido(s), %d pulado(s), %d com falha. Manifesto: %s",
        converted,
        skipped,
        len(failed),
        manifest_path,
    )
    for r in failed:
        log.error("%s (%s): %s", r.source_zip, r.member or "-", r.error)
    return 1 if failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.bronze",
        description="Converte os zips de data/raw/<mes>/ em Parquet na camada bronze.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument(
        "--tables",
        nargs="+",
        metavar="TABELA",
        help=f"Tabelas a processar (padrao: todas). Validas: {', '.join(config.ALL_TABLES)}",
    )
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DIR)
    parser.add_argument("--bronze-dir", type=Path, default=config.BRONZE_DIR)
    parser.add_argument("--force", action="store_true", help="Refaz arquivos que ja existem na bronze")
    parser.add_argument(
        "--memory-limit",
        default="4GB",
        help="Limite de memoria do DuckDB; o excedente vai para disco (padrao: %(default)s)",
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    parser.add_argument(
        "--max-reject-ratio",
        type=float,
        default=0.001,
        help="Fracao maxima de linhas rejeitadas por arquivo antes de falhar (padrao: %(default)s)",
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
    if not 0 <= args.max_reject_ratio <= 1:
        print("--max-reject-ratio deve estar entre 0 e 1", file=sys.stderr)
        return 2
    try:
        tables = normalize_tables(args.tables)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    return run(
        args.month,
        tables,
        raw_dir=args.raw_dir,
        bronze_dir=args.bronze_dir,
        force=args.force,
        memory_limit=args.memory_limit,
        threads=args.threads,
        max_reject_ratio=args.max_reject_ratio,
        tmp_root=config.DATA_DIR / "tmp",
    )


if __name__ == "__main__":
    sys.exit(main())
