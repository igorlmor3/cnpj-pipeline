"""Profiling da camada bronze: mede a qualidade dos dados e gera relatorios.

Para cada tabela, lê os Parquet de data/bronze/<tabela>/reference_month=<mes>/ e calcula
por coluna: nulos, valores distintos, tamanho do texto, espacos nas bordas, caracteres de
controle, padroes de formato, valores sentinela, validade de datas e formato de decimais.
No nivel da tabela mede duplicidade da chave de negocio e, opcionalmente, a integridade
referencial entre tabelas.

Privacidade: os relatorios contem apenas agregados. Valores literais so aparecem para
colunas categoricas de baixa sensibilidade (codigos e indicadores). Colunas com nomes,
documentos, enderecos e contatos aparecem somente como padroes mascarados
(digitos viram 9, letras viram A) e contagens.

Saida: <out-dir>/<mes>/<tabela>.json e .md, mais referential.json e .md.

Exemplos:
    python -m cnpj_pipeline.profiling --month 2026-10
    python -m cnpj_pipeline.profiling --month 2026-10 --tables empresas --sample-percent 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE, normalize_tables
from cnpj_pipeline.schemas import columns_for

log = logging.getLogger("cnpj_pipeline.profiling")

# Valores que costumam representar "sem informacao" em dados cadastrais.
SENTINELS = (
    "0",
    "00",
    "000",
    "0000",
    "00000",
    "000000",
    "0000000",
    "00000000",
    "99999999",
    "-",
    ".",
    "N/A",
    "NULL",
    "NAO INFORMADO",
    "SEM INFORMACAO",
)

# Colunas cujos valores literais podem aparecer no relatorio (categoricas, baixa sensibilidade).
SHOW_VALUES = frozenset(
    {
        "natureza_juridica",
        "qualificacao_responsavel",
        "porte_empresa",
        "identificador_matriz_filial",
        "situacao_cadastral",
        "motivo_situacao_cadastral",
        "pais",
        "uf",
        "situacao_especial",
        "identificador_socio",
        "qualificacao_socio",
        "qualificacao_representante_legal",
        "faixa_etaria",
        "opcao_pelo_simples",
        "opcao_pelo_mei",
        "codigo",
        "descricao",
    }
)
MAX_TOP_VALUES = 15
MAX_DISTINCT_FOR_VALUES = 100
MAX_LEN_FOR_PATTERNS = 20

DECIMAL_COMMA_COLUMNS = frozenset({"capital_social"})

# Chave de negocio presumida de cada tabela (hipotese a confirmar com o dicionario).
KEYS: dict[str, tuple[str, ...]] = {
    "Empresas": ("cnpj_basico",),
    "Estabelecimentos": ("cnpj_basico", "cnpj_ordem", "cnpj_dv"),
    "Simples": ("cnpj_basico",),
    "Cnaes": ("codigo",),
    "Motivos": ("codigo",),
    "Municipios": ("codigo",),
    "Naturezas": ("codigo",),
    "Paises": ("codigo",),
    "Qualificacoes": ("codigo",),
}

# Relacoes presumidas: (tabela filha, coluna filha, tabela pai, coluna pai).
RELATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("Estabelecimentos", "cnpj_basico", "Empresas", "cnpj_basico"),
    ("Estabelecimentos", "cnae_fiscal_principal", "Cnaes", "codigo"),
    ("Estabelecimentos", "municipio", "Municipios", "codigo"),
    ("Estabelecimentos", "motivo_situacao_cadastral", "Motivos", "codigo"),
    ("Estabelecimentos", "pais", "Paises", "codigo"),
    ("Empresas", "natureza_juridica", "Naturezas", "codigo"),
    ("Empresas", "qualificacao_responsavel", "Qualificacoes", "codigo"),
    ("Socios", "cnpj_basico", "Empresas", "cnpj_basico"),
    ("Socios", "qualificacao_socio", "Qualificacoes", "codigo"),
    ("Simples", "cnpj_basico", "Empresas", "cnpj_basico"),
)


# --------------------------------------------------------------------------- #
# Utilitarios
# --------------------------------------------------------------------------- #


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 4) if total else 0.0


def parquet_glob(bronze_dir: Path, table: str, month: str) -> Path:
    return bronze_dir / table.lower() / f"reference_month={month}" / "*.parquet"


def has_parquet(bronze_dir: Path, table: str, month: str) -> bool:
    return any(parquet_glob(bronze_dir, table, month).parent.glob("*.parquet"))


def _is_date_column(column: str) -> bool:
    return column.startswith("data_")


# --------------------------------------------------------------------------- #
# Profiling por coluna
# --------------------------------------------------------------------------- #


def profile_column(con: duckdb.DuckDBPyConnection, view: str, column: str, total: int) -> dict:
    c = _ident(column)
    sentinel_list = ", ".join(_q(s) for s in SENTINELS)

    row = con.execute(
        f"""
        SELECT count({c}),
               approx_count_distinct({c}),
               min(length({c})),
               max(length({c})),
               count(*) FILTER (WHERE {c} <> trim({c})),
               count(*) FILTER (WHERE {c} IS NOT NULL AND trim({c}) = ''),
               count(*) FILTER (WHERE regexp_matches({c}, '[[:cntrl:]]')),
               count(*) FILTER (WHERE contains({c}, chr(65533)))
        FROM {view}
        """
    ).fetchone()
    non_null, distinct, min_len, max_len = row[0], row[1], row[2], row[3]

    info: dict = {
        "non_null": non_null,
        "nulls": total - non_null,
        "null_pct": _pct(total - non_null, total),
        "distinct_approx": distinct,
        "min_length": min_len,
        "max_length": max_len,
        "edge_whitespace": row[4],
        "blank": row[5],
        "control_chars": row[6],
        "replacement_chars": row[7],
    }
    if non_null == 0:
        return info

    sentinels = con.execute(
        f"""
        SELECT {c}, count(*) AS n FROM {view}
        WHERE {c} IN ({sentinel_list}) GROUP BY 1 ORDER BY n DESC
        """
    ).fetchall()
    info["sentinels"] = [{"value": v, "count": n, "pct": _pct(n, total)} for v, n in sentinels]

    if max_len is not None and max_len <= MAX_LEN_FOR_PATTERNS:
        patterns = con.execute(
            f"""
            SELECT regexp_replace(regexp_replace({c}, '[0-9]', '9', 'g'), '[A-Za-z]', 'A', 'g') AS p,
                   count(*) AS n
            FROM {view} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 8
            """
        ).fetchall()
        info["patterns"] = [{"pattern": p, "count": n, "pct": _pct(n, non_null)} for p, n in patterns]

    if column in SHOW_VALUES and distinct <= MAX_DISTINCT_FOR_VALUES:
        values = con.execute(
            f"""
            SELECT {c}, count(*) AS n FROM {view}
            WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT {MAX_TOP_VALUES}
            """
        ).fetchall()
        info["top_values"] = [{"value": v, "count": n, "pct": _pct(n, non_null)} for v, n in values]

    if _is_date_column(column):
        parsed = f"try_strptime({c}, '%Y%m%d')"
        strict = f"regexp_matches({c}, '^[0-9]{{8}}$')"
        valid, sentinel, low, high = con.execute(
            f"""
            SELECT count(*) FILTER (WHERE {strict} AND {parsed} IS NOT NULL),
                   count(*) FILTER (WHERE {c} IN ('0', '00000000')),
                   strftime(min(CASE WHEN {strict} THEN {parsed} END), '%Y-%m-%d'),
                   strftime(max(CASE WHEN {strict} THEN {parsed} END), '%Y-%m-%d')
            FROM {view}
            """
        ).fetchone()
        info["date"] = {
            "valid": valid,
            "sentinel_zero": sentinel,
            "invalid": non_null - valid - sentinel,
            "min_valid": low,
            "max_valid": high,
        }

    if column in DECIMAL_COMMA_COLUMNS:
        matching, with_dot, max_value = con.execute(
            f"""
            SELECT count(*) FILTER (WHERE regexp_matches({c}, '^[0-9]+,[0-9]+$')),
                   count(*) FILTER (WHERE contains({c}, '.')),
                   max(try_cast(replace({c}, ',', '.') AS DOUBLE))
            FROM {view}
            """
        ).fetchone()
        info["decimal_comma"] = {
            "matching": matching,
            "not_matching": non_null - matching,
            "with_dot": with_dot,
            "max_value": max_value,
        }
    return info


def profile_table(
    con: duckdb.DuckDBPyConnection,
    bronze_dir: Path,
    table: str,
    month: str,
    *,
    sample_percent: float,
) -> dict:
    glob = parquet_glob(bronze_dir, table, month)
    n_files = len(list(glob.parent.glob("*.parquet")))
    source = f"read_parquet({_q(glob.as_posix())}, hive_partitioning=false)"

    view = f"prof_{table.lower()}"
    if sample_percent < 100:
        con.execute(
            f"CREATE OR REPLACE TABLE {view} AS SELECT * FROM {source} "
            f"USING SAMPLE {sample_percent} PERCENT (bernoulli, 42)"
        )
    else:
        con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {source}")

    total = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    log.info("%s: perfilando %d linha(s) em %d arquivo(s)", table, total, n_files)

    columns = {}
    for column in columns_for(table):
        columns[column] = profile_column(con, view, column, total)

    result: dict = {
        "table": table,
        "reference_month": month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": n_files,
        "rows_profiled": total,
        "sample_percent": sample_percent,
        "columns": columns,
    }

    key = KEYS.get(table)
    if key:
        key_cols = ", ".join(_ident(k) for k in key)
        dup_keys, dup_rows = con.execute(
            f"""
            SELECT count(*), coalesce(sum(n), 0) FROM (
                SELECT count(*) AS n FROM {view} GROUP BY {key_cols} HAVING count(*) > 1
            )
            """
        ).fetchone()
        result["key"] = {
            "columns": list(key),
            "duplicated_keys": dup_keys,
            "rows_in_duplicated_keys": int(dup_rows),
        }
    return result


# --------------------------------------------------------------------------- #
# Integridade referencial
# --------------------------------------------------------------------------- #


def check_relations(con: duckdb.DuckDBPyConnection, bronze_dir: Path, month: str) -> list[dict]:
    """Conta valores da tabela filha sem correspondente na tabela pai (chaves orfas)."""
    findings = []
    for child, child_col, parent, parent_col in RELATIONS:
        if not (has_parquet(bronze_dir, child, month) and has_parquet(bronze_dir, parent, month)):
            continue
        child_src = parquet_glob(bronze_dir, child, month).as_posix()
        parent_src = parquet_glob(bronze_dir, parent, month).as_posix()
        cc, pc = _ident(child_col), _ident(parent_col)
        checked, orphans = con.execute(
            f"""
            SELECT count(*) FILTER (WHERE c.{cc} IS NOT NULL),
                   count(*) FILTER (WHERE c.{cc} IS NOT NULL AND p.k IS NULL)
            FROM read_parquet({_q(child_src)}, hive_partitioning=false) c
            LEFT JOIN (
                SELECT DISTINCT {pc} AS k
                FROM read_parquet({_q(parent_src)}, hive_partitioning=false)
            ) p ON c.{cc} = p.k
            """
        ).fetchone()
        findings.append(
            {
                "child": child,
                "child_column": child_col,
                "parent": parent,
                "parent_column": parent_col,
                "checked_rows": checked,
                "orphan_rows": orphans,
                "orphan_pct": _pct(orphans, checked),
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Achados e relatorios
# --------------------------------------------------------------------------- #


def findings_for_table(profile: dict) -> list[str]:
    """Resume em frases o que merece uma regra de qualidade."""
    out = []
    for name, col in profile["columns"].items():
        if col["non_null"] == 0:
            out.append(f"`{name}`: todos os valores são nulos")
            continue
        if col["null_pct"] >= 50:
            out.append(f"`{name}`: {col['null_pct']}% de nulos")
        for s in col.get("sentinels", []):
            out.append(
                f"`{name}`: valor candidato a sentinela `{s['value']}` em {s['count']} linha(s) ({s['pct']}%)"
            )
        date = col.get("date")
        if date and date["invalid"]:
            out.append(f"`{name}`: {date['invalid']} data(s) fora do formato AAAAMMDD ou inexistente(s)")
        dec = col.get("decimal_comma")
        if dec and dec["not_matching"]:
            out.append(f"`{name}`: {dec['not_matching']} valor(es) fora do padrão `0,00`")
        if col["edge_whitespace"]:
            out.append(f"`{name}`: {col['edge_whitespace']} valor(es) com espaços nas bordas")
        if col["blank"]:
            out.append(f"`{name}`: {col['blank']} valor(es) só com espaços")
        if col["control_chars"]:
            out.append(f"`{name}`: {col['control_chars']} valor(es) com caracteres de controle")
        if col["replacement_chars"]:
            out.append(
                f"`{name}`: {col['replacement_chars']} valor(es) com caractere de substituição "
                "(problema de encoding)"
            )
    key = profile.get("key")
    if key and key["duplicated_keys"]:
        out.append(
            f"chave `{', '.join(key['columns'])}`: {key['duplicated_keys']} chave(s) duplicada(s) "
            f"em {key['rows_in_duplicated_keys']} linha(s)"
        )
    return out


def render_table_markdown(profile: dict) -> str:
    lines = [
        f"# Profiling: {profile['table']} ({profile['reference_month']})",
        "",
        f"- Arquivos Parquet: {profile['files']}",
        f"- Linhas analisadas: {profile['rows_profiled']:,}".replace(",", "."),
    ]
    if profile["sample_percent"] < 100:
        lines.append(f"- Amostra: {profile['sample_percent']}% das linhas (resultados aproximados)")
    lines += ["- Dados pessoais: este relatório contém apenas agregados e padrões mascarados", ""]

    found = findings_for_table(profile)
    lines += ["## Achados automáticos", ""]
    lines += [f"- {f}" for f in found] if found else ["Nenhum achado automático."]
    lines.append("")

    key = profile.get("key")
    if key:
        lines += [
            "## Chave de negócio presumida",
            "",
            f"Colunas: `{', '.join(key['columns'])}`. Chaves duplicadas: {key['duplicated_keys']} "
            f"({key['rows_in_duplicated_keys']} linhas).",
            "",
        ]

    lines += [
        "## Resumo por coluna",
        "",
        "| Coluna | Nulos (%) | Distintos (aprox.) | Tamanho mín-máx | Sentinelas | Espaços | Controle |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, col in profile["columns"].items():
        sentinels = sum(s["count"] for s in col.get("sentinels", []))
        size = f"{col['min_length']}-{col['max_length']}" if col["max_length"] is not None else "-"
        lines.append(
            f"| `{name}` | {col['null_pct']} | {col['distinct_approx']} | {size} | {sentinels} "
            f"| {col['edge_whitespace']} | {col['control_chars']} |"
        )
    lines.append("")

    lines += ["## Detalhes", ""]
    for name, col in profile["columns"].items():
        parts = []
        if "patterns" in col:
            items = ", ".join(f"`{p['pattern']}` ({p['pct']}%)" for p in col["patterns"][:5])
            parts.append(f"- Padrões de formato: {items}")
        if "top_values" in col:
            items = ", ".join(f"`{v['value']}` ({v['pct']}%)" for v in col["top_values"][:10])
            parts.append(f"- Valores mais frequentes: {items}")
        if "date" in col:
            d = col["date"]
            parts.append(
                f"- Datas: {d['valid']} válidas, {d['sentinel_zero']} zeradas, {d['invalid']} inválidas; "
                f"menor {d['min_valid']}, maior {d['max_valid']}"
            )
        if "decimal_comma" in col:
            d = col["decimal_comma"]
            parts.append(
                f"- Decimal: {d['matching']} no padrão `0,00`, {d['not_matching']} fora do padrão; "
                f"maior valor {d['max_value']}"
            )
        if parts:
            lines += [f"### `{name}`", "", *parts, ""]
    return "\n".join(lines)


def render_referential_markdown(month: str, findings: list[dict]) -> str:
    lines = [
        f"# Integridade referencial ({month})",
        "",
        "Relações presumidas entre tabelas. Se uma das tabelas estiver incompleta (nem todas as partes",
        "foram processadas), os órfãos refletem a ausência de arquivos e não um problema nos dados.",
        "",
        "| Filha | Coluna | Pai | Coluna | Linhas checadas | Órfãs | Órfãs (%) |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for f in findings:
        lines.append(
            f"| {f['child']} | `{f['child_column']}` | {f['parent']} | `{f['parent_column']}` "
            f"| {f['checked_rows']} | {f['orphan_rows']} | {f['orphan_pct']} |"
        )
    if not findings:
        lines.append("| - | - | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orquestracao e CLI
# --------------------------------------------------------------------------- #


def run(
    month: str,
    tables: list[str],
    *,
    bronze_dir: Path,
    out_dir: Path,
    memory_limit: str,
    threads: int | None,
    sample_percent: float,
    skip_referential: bool,
) -> int:
    present = [t for t in tables if has_parquet(bronze_dir, t, month)]
    for table in tables:
        if table not in present:
            log.warning("Tabela %s: sem Parquet na bronze para %s, pulando", table, month)
    if not present:
        log.error("Nada para perfilar. Rode antes: python -m cnpj_pipeline.bronze --month %s", month)
        return 1

    month_out = out_dir / month
    month_out.mkdir(parents=True, exist_ok=True)

    options: dict[str, str | int] = {"memory_limit": memory_limit}
    if threads:
        options["threads"] = threads
    con = duckdb.connect(config=options)
    try:
        for table in present:
            profile = profile_table(con, bronze_dir, table, month, sample_percent=sample_percent)
            stem = table.lower()
            (month_out / f"{stem}.json").write_text(
                json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (month_out / f"{stem}.md").write_text(render_table_markdown(profile), encoding="utf-8")
            log.info("%s: relatório em %s", table, month_out / f"{stem}.md")

        if not skip_referential:
            findings = check_relations(con, bronze_dir, month)
            (month_out / "referential.json").write_text(
                json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (month_out / "referential.md").write_text(
                render_referential_markdown(month, findings), encoding="utf-8"
            )
            log.info("Integridade referencial: %d relação(ões) checada(s)", len(findings))
    except duckdb.Error as exc:
        log.error("Erro do DuckDB durante o profiling: %s", exc)
        return 1
    finally:
        con.close()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.profiling",
        description="Gera relatórios de qualidade de dados a partir da camada bronze.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument(
        "--tables",
        nargs="+",
        metavar="TABELA",
        help=f"Tabelas (padrao: todas com Parquet). Validas: {', '.join(config.ALL_TABLES)}",
    )
    parser.add_argument("--bronze-dir", type=Path, default=config.BRONZE_DIR)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=config.PROJECT_ROOT / "docs" / "profiling",
        help="Pasta dos relatorios (padrao: docs/profiling)",
    )
    parser.add_argument(
        "--memory-limit", default="4GB", help="Limite de memoria do DuckDB (padrao: %(default)s)"
    )
    parser.add_argument("--threads", type=int, default=None, help="Threads do DuckDB (padrao: automatico)")
    parser.add_argument(
        "--sample-percent",
        type=float,
        default=100.0,
        help="Perfila apenas esta porcentagem das linhas, para tabelas grandes (padrao: %(default)s)",
    )
    parser.add_argument(
        "--skip-referential", action="store_true", help="Nao calcula a integridade referencial (mais rapido)"
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
    if not 0 < args.sample_percent <= 100:
        print("--sample-percent deve estar entre 0 (exclusivo) e 100", file=sys.stderr)
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
        out_dir=args.out_dir,
        memory_limit=args.memory_limit,
        threads=args.threads,
        sample_percent=args.sample_percent,
        skip_referential=args.skip_referential,
    )


if __name__ == "__main__":
    sys.exit(main())
