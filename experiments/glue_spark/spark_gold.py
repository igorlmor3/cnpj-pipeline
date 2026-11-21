"""Constroi com Spark o fato e os marts da gold que dependem dele, a partir da silver de um mes.

E a metade Spark de um experimento de comparacao com o DuckDB (veja README.md desta pasta). O SQL e o
mesmo de `gold_models.py`: o job importa o proprio arquivo do projeto, e a unica traducao feita aqui
e a de funcoes que o Spark nao tem com o mesmo nome (`to_spark`). Assim, uma diferenca de resultado
nao vem de uma regra reescrita.

Modelos construidos: as dimensoes que os marts usam, `fato_estabelecimento` e quatro marts
(aberturas, estabelecimentos ativos, perfil por porte e natureza, Simples e MEI por UF). O mart de
socios fica de fora, porque exigiria os nomes dos socios na nuvem.

Entrada (por tabela): <input-dir>/<tabela>/reference_month=<mes>/*.parquet, com as colunas que o SQL
usa (veja `make_input.py`). Saida: <output-dir>/<modelo>/reference_month=<mes>/ em Parquet.

Na AWS Glue (versao 5.0, Spark 3.5), `gold_models.py` vai junto pelo argumento `--extra-py-files`. Para
rodar no computador: `python spark_gold.py --local --month 2000-01 --input-dir ... --output-dir ...`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

try:
    from cnpj_pipeline import gold_models as gm
except ImportError:  # na Glue o arquivo chega por --extra-py-files, fora do pacote
    import gold_models as gm  # type: ignore[no-redef]

# Tabelas da silver lidas (nomes como em config.ALL_TABLES) e, de cada uma, as colunas que o SQL usa.
# Nenhuma e nome de pessoa, endereco ou contato: so isso sobe para a nuvem (veja make_input.py).
INPUT_COLUMNS: dict[str, tuple[str, ...]] = {
    "Estabelecimentos": (
        "cnpj",
        "cnpj_basico",
        "cnpj_ordem",
        "identificador_matriz_filial",
        "situacao_cadastral",
        "data_situacao_cadastral",
        "motivo_situacao_cadastral",
        "data_inicio_atividade",
        "cnae_fiscal_principal",
        "uf",
        "municipio",
    ),
    "Empresas": ("cnpj_basico", "natureza_juridica", "porte_empresa", "capital_social"),
    "Simples": ("cnpj_basico", "opcao_pelo_simples", "opcao_pelo_mei"),
    "Naturezas": ("codigo", "descricao"),
    "Municipios": ("codigo", "descricao"),
}
INPUT_TABLES = tuple(INPUT_COLUMNS)
# Modelos construidos, em ordem de dependencia.
MODEL_NAMES = (
    "dim_cnae_secao",
    "dim_municipio",
    "dim_natureza_juridica",
    "dim_porte",
    "fato_estabelecimento",
    "mart_aberturas_mensal",
    "mart_estabelecimentos_ativos",
    "mart_perfil_porte_natureza",
    "mart_simples_mei_uf",
)
EXCLUDED_FROM_COMPARISON = ("_gold_processed_at",)
# A mediana de decimais em grupos de tamanho par e a media dos dois valores do meio: o DuckDB trunca o
# resultado no centavo e o Spark (que calcula em ponto flutuante) o arredonda. Essas colunas sao
# conferidas com a tolerancia de 1 centavo, e o resto da tabela, linha a linha.
TOLERANT_COLUMNS = {
    "mart_perfil_porte_natureza": {
        "columns": ("capital_social_mediano",),
        "keys": ("porte_empresa", "natureza_juridica", "is_organizacao_politica"),
        "tolerance": 0.01,
    }
}
SMALL_MODEL_ROWS = 2_000_000  # abaixo disso a conferencia compara linha a linha; acima, por checksum


def to_spark(sql: str) -> str:
    """Traduz o SQL do DuckDB para o Spark. Duas coisas mudam: `strftime` (vira `date_format`) e o tipo
    `VARCHAR` sem tamanho (vira `STRING`). Qualquer outra coisa nao suportada aparece como erro do
    Spark, e nao como resultado diferente."""
    translated = re.sub(r"strftime\(([^,()]+), '%Y-%m'\)", r"date_format(\1, 'yyyy-MM')", sql)
    if "strftime" in translated:
        raise ValueError("strftime com formato nao previsto na traducao para o Spark")
    return re.sub(r"\bAS VARCHAR\b", "AS STRING", translated)


def join_path(base: str, *parts: str) -> str:
    return "/".join([base.rstrip("/"), *parts])


def month_dir(base: str, name: str, month: str) -> str:
    return join_path(base, name, f"reference_month={month}")


def register_inputs(spark: Any, input_dir: str, month: str) -> None:
    for table in INPUT_TABLES:
        path = month_dir(input_dir, table.lower(), month)
        spark.read.parquet(path).createOrReplaceTempView(f"silver_{table.lower()}")


def build(spark: Any, *, input_dir: str, output_dir: str, month: str, processed_at: str) -> list[dict]:
    """Constroi os modelos em ordem e devolve linhas, tempo e arquivos de cada um."""
    register_inputs(spark, input_dir, month)
    results = []
    for name in MODEL_NAMES:
        model = gm.MODELS_BY_NAME[name]
        started = time.monotonic()
        frame = spark.sql(to_spark(model.sql(month, processed_at)))
        target = month_dir(output_dir, name, month)
        writer = frame if model.kind == "fato" else frame.coalesce(1)
        writer.write.mode("overwrite").parquet(target)
        # Como no DuckDB, os modelos seguintes leem o que foi gravado, e nao o plano do anterior.
        written = spark.read.parquet(target)
        written.createOrReplaceTempView(f"gold_{name}")
        rows = written.count()
        seconds = time.monotonic() - started
        results.append({"modelo": name, "linhas": rows, "segundos": round(seconds, 1)})
        print(f"MODELO {name}: {rows} linhas em {seconds:.1f} s", flush=True)
    return results


def canonical(frame: Any, columns: Sequence[str]) -> Any:
    """Colunas comparaveis entre o Parquet do DuckDB e o do Spark: numeros fracionarios com a mesma escala."""
    from pyspark.sql import functions as F
    from pyspark.sql import types as T

    selected = []
    for field in frame.select(*columns).schema.fields:
        column = F.col(field.name)
        if isinstance(field.dataType, (T.DoubleType, T.FloatType, T.DecimalType)):
            column = column.cast("decimal(38,4)")
        selected.append(column.alias(field.name))
    return frame.select(*selected)


def tolerance_check(mine: Any, theirs: Any, rule: dict) -> dict:
    """Confere colunas com tolerancia: junta pelas chaves (nulos iguais entre si) e mede a maior diferenca."""
    from pyspark.sql import functions as F

    keys = list(rule["keys"])
    condition = None
    for key in keys:
        piece = mine[key].eqNullSafe(theirs[key])
        condition = piece if condition is None else condition & piece
    joined = mine.alias("a").join(theirs.alias("b"), condition, "full_outer")
    result: dict[str, Any] = {"tolerancia": rule["tolerance"]}
    ok = True
    for column in rule["columns"]:
        a, b = F.col(f"a.{column}").cast("decimal(38,4)"), F.col(f"b.{column}").cast("decimal(38,4)")
        stats = joined.select(
            F.max(F.abs(a - b)).alias("maxima"),
            F.sum(F.when(a.isNull() != b.isNull(), 1).otherwise(0)).alias("nulos_diferentes"),
            F.sum(F.when(a != b, 1).otherwise(0)).alias("diferentes"),
        ).first()
        maxima = float(stats["maxima"] or 0)
        result[f"{column}_diferenca_maxima"] = maxima
        result[f"{column}_grupos_diferentes"] = int(stats["diferentes"] or 0)
        ok = ok and maxima <= rule["tolerance"] + 1e-9 and int(stats["nulos_diferentes"] or 0) == 0
    result["dentro_da_tolerancia"] = ok
    return result


def compare(spark: Any, output_dir: str, reference_dir: str, month: str) -> list[dict]:
    """Compara cada modelo gravado pelo Spark com o gravado pelo DuckDB (gold publicada)."""
    from pyspark.sql import functions as F

    report = []
    for name in MODEL_NAMES:
        mine = spark.read.parquet(month_dir(output_dir, name, month))
        theirs = spark.read.parquet(month_dir(reference_dir, name, month))
        tolerant = TOLERANT_COLUMNS.get(name)
        skipped = set(EXCLUDED_FROM_COMPARISON) | set(tolerant["columns"] if tolerant else ())
        shared = [c for c in mine.columns if c in theirs.columns and c not in skipped]
        only_one_side = (set(mine.columns) ^ set(theirs.columns)) - set(EXCLUDED_FROM_COMPARISON)
        mine_rows, theirs_rows = mine.count(), theirs.count()
        entry: dict[str, Any] = {
            "modelo": name,
            "linhas_spark": mine_rows,
            "linhas_duckdb": theirs_rows,
            "colunas_diferentes": sorted(only_one_side),
        }
        left, right = canonical(mine, shared), canonical(theirs, shared)
        if mine_rows <= SMALL_MODEL_ROWS and theirs_rows <= SMALL_MODEL_ROWS:
            entry["metodo"] = "exceptAll nos dois sentidos"
            entry["linhas_so_no_spark"] = left.exceptAll(right).count()
            entry["linhas_so_no_duckdb"] = right.exceptAll(left).count()
            entry["igual"] = (
                mine_rows == theirs_rows
                and not only_one_side
                and entry["linhas_so_no_spark"] == 0
                and entry["linhas_so_no_duckdb"] == 0
            )
        else:
            entry["metodo"] = "soma de xxhash64 das linhas"
            sums = []
            for frame in (left, right):
                total = frame.select(F.sum(F.xxhash64(*shared).cast("decimal(38,0)")).alias("s")).first()["s"]
                sums.append(str(total))
            entry["checksum_spark"], entry["checksum_duckdb"] = sums
            entry["igual"] = mine_rows == theirs_rows and not only_one_side and sums[0] == sums[1]
        if tolerant:
            within = tolerance_check(mine, theirs, tolerant)
            entry.update(within)
            entry["igual"] = entry["igual"] and within["dentro_da_tolerancia"]
        report.append(entry)
        print(
            f"CONFERENCIA {name}: {'IGUAL' if entry['igual'] else 'DIFERENTE'} ({entry['metodo']})",
            flush=True,
        )
    return report


def write_summary(output_dir: str, summary: dict[str, Any], processed_at: str) -> str:
    """Grava o resumo em JSON ao lado do resultado (S3 ou pasta local) e devolve onde ficou."""
    name = "_resumo_" + processed_at.replace("-", "").replace(":", "").replace(" ", "T") + ".json"
    body = json.dumps(summary, ensure_ascii=False, indent=2)
    target = join_path(output_dir, name)
    if target.startswith("s3://"):
        import boto3  # disponivel na Glue

        bucket, _, key = target[len("s3://") :].partition("/")
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))
    else:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gold com Spark (experimento)")
    parser.add_argument("--month", required=True, help="Mes de referencia AAAA-MM")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--verify-dir", help="Gold gravada pelo DuckDB, para conferir o resultado")
    parser.add_argument("--local", action="store_true", help="Spark local, para teste no computador")
    # A Glue acrescenta argumentos proprios (--JOB_NAME, --JOB_ID...): sao ignorados.
    args, _ = parser.parse_known_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        parser.error("--month precisa estar no formato AAAA-MM")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from pyspark.sql import SparkSession

    builder = SparkSession.builder.appName("cnpj-gold-spark").config("spark.sql.session.timeZone", "UTC")
    if args.local:
        builder = builder.master("local[2]").config("spark.sql.shuffle.partitions", "4")
    spark = builder.getOrCreate()
    processed_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    started = time.monotonic()
    results = build(
        spark,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        month=args.month,
        processed_at=processed_at,
    )
    build_seconds = time.monotonic() - started
    print(f"CONSTRUCAO: {build_seconds:.1f} s", flush=True)
    summary: dict[str, Any] = {
        "mes": args.month,
        "spark": spark.version,
        "paralelismo": spark.sparkContext.defaultParallelism,
        "construcao_segundos": round(build_seconds, 1),
        "modelos": results,
    }
    exit_code = 0
    if args.verify_dir:
        summary["conferencia"] = compare(spark, args.output_dir, args.verify_dir, args.month)
        if not all(entry["igual"] for entry in summary["conferencia"]):
            exit_code = 3
    print(f"RESUMO GRAVADO EM {write_summary(args.output_dir, summary, processed_at)}", flush=True)
    print("RESUMO " + json.dumps(summary, ensure_ascii=False), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
