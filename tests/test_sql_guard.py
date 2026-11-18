"""Testes da guarda de SQL do agente: o que passa, o que e barrado e o que e reescrito."""

from __future__ import annotations

import duckdb
import pytest

from cnpj_pipeline.agent import sql_guard
from cnpj_pipeline.agent.sql_guard import ALLOWED_TABLES, MAX_ROWS, SqlRejected, guard

MONTH = "2026-10"
MART = "gold_mart_simples_mei_uf"


def run(sql: str, **kwargs):
    return guard(sql, month=MONTH, **kwargs)


# --------------------------------------------------------------------------- #
# O que passa
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT uf, empresas_ativas FROM {MART} ORDER BY empresas_ativas DESC",
        f"select UF from {MART.upper()}",  # maiusculas
        f'SELECT "uf" FROM "{MART}"',  # identificadores entre aspas
        f"SELECT uf FROM cnpj.{MART}",  # banco permitido
        f"SELECT a.uf FROM {MART} a JOIN gold_dim_cnae_secao s ON a.uf = s.codigo",
        f"WITH t AS (SELECT uf FROM {MART}) SELECT * FROM t",
        f"SELECT uf FROM {MART} UNION ALL SELECT uf FROM {MART}",
        f"SELECT uf FROM {MART} WHERE uf = 'a;--b'",  # ponto e virgula e '--' dentro de texto
        f"SELECT uf FROM {MART};",  # ponto e virgula final
        "SELECT 1 + 1",  # sem tabela
    ],
)
def test_valid_selects_pass(sql):
    result = run(sql)
    assert result.sql.upper().startswith(("SELECT", "WITH"))
    assert result.limit == MAX_ROWS


def test_tables_read_are_reported():
    result = run(f"SELECT 1 FROM {MART} a JOIN gold_dim_porte p ON a.uf = p.codigo")
    assert result.tables == ("gold_dim_porte", MART)


def test_text_literals_survive_the_rewrite():
    assert "'a;--b'" in run(f"SELECT uf FROM {MART} WHERE uf = 'a;--b'").sql


# --------------------------------------------------------------------------- #
# O que e barrado
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT 1; SELECT 2",
        f"SELECT 1; DROP TABLE {MART}",
        f"DROP TABLE {MART}",
        f"DELETE FROM {MART}",
        f"INSERT INTO {MART} VALUES (1)",
        f"UPDATE {MART} SET uf = 'X'",
        "CREATE TABLE x AS SELECT 1",
        f"CREATE VIEW v AS SELECT * FROM {MART}",
        "ALTER TABLE x ADD COLUMN y int",
        "SHOW TABLES",
        "DESCRIBE gold_dim_porte",
        "EXPLAIN SELECT 1",
        "SET SESSION x = 1",
        "USE other",
        "CALL system.foo()",
        "SELEC FROM",  # sintaxe invalida
        f"SELECT * FROM {MART} WHERE",  # incompleto
    ],
)
def test_non_select_and_invalid_sql_is_rejected(sql):
    with pytest.raises(SqlRejected):
        run(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM gold_fato_estabelecimento",
        "SELECT * FROM cnpj.gold_fato_estabelecimento",
        'SELECT * FROM "gold_fato_estabelecimento"',
        "SELECT * FROM GOLD_FATO_ESTABELECIMENTO",
        "SELECT * FROM silver_estabelecimentos",
        "SELECT * FROM silver_empresas",
        "SELECT * FROM bronze_socios",
        'SELECT * FROM "gold_dim_cnae$partitions"',
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM information_schema.columns",
        "SELECT * FROM other.gold_dim_porte",
        "SELECT * FROM awsdatacatalog.cnpj.gold_dim_porte",
        "SELECT * FROM awsdatacatalog.information_schema.tables",
        "SELECT * FROM table(foo(1))",
        "SELECT * FROM unnest(array[1,2])",
        f"SELECT * FROM {MART} WHERE uf IN (SELECT cnpj FROM gold_fato_estabelecimento)",
        f"SELECT (SELECT count(*) FROM silver_socios) FROM {MART}",
        f"SELECT * FROM {MART} a JOIN silver_socios s ON a.uf = s.qualificacao_socio",
    ],
)
def test_tables_outside_the_allowed_list_are_rejected(sql):
    with pytest.raises(SqlRejected):
        run(sql)


def test_the_message_lists_available_tables():
    with pytest.raises(SqlRejected, match="gold_mart_simples_mei_uf"):
        run("SELECT * FROM gold_fato_estabelecimento")


def test_fato_is_not_allowed():
    assert "gold_fato_estabelecimento" not in ALLOWED_TABLES
    assert not any(t.startswith("silver_") or t.startswith("bronze_") for t in ALLOWED_TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        # CTE com nome de tabela real, definida em um lugar, para esconder o uso real em outro
        "SELECT * FROM (WITH silver_socios AS (SELECT 1 a) SELECT * FROM silver_socios) q "
        "JOIN silver_socios s ON 1 = 1",
        "WITH x AS (SELECT * FROM (WITH silver_socios AS (SELECT 1) SELECT * FROM silver_socios) q) "
        "SELECT * FROM x JOIN silver_socios s ON 1 = 1",
        # subconsulta que define o apelido de outra tabela
        f"SELECT * FROM {MART} a JOIN (SELECT 1 FROM gold_fato_estabelecimento) b ON 1 = 1",
    ],
)
def test_cte_names_cannot_hide_a_real_table(sql):
    with pytest.raises(SqlRejected):
        run(sql)


def test_a_cte_shadowing_a_real_table_reads_nothing_real():
    # a CTE vence a tabela no escopo dela, como no motor; nao ha acesso ao fato
    result = run("WITH gold_fato_estabelecimento AS (SELECT 1 AS x) SELECT x FROM gold_fato_estabelecimento")
    assert result.tables == ()


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {MART} a, gold_dim_porte b",  # juncao por virgula
        f"SELECT * FROM {MART} a CROSS JOIN gold_dim_porte b",
        f"SELECT * FROM {MART} a NATURAL JOIN gold_dim_porte b",
        f"SELECT * FROM {MART} a JOIN gold_dim_porte b",  # sem condicao
    ],
)
def test_joins_without_condition_are_rejected(sql):
    with pytest.raises(SqlRejected, match="condicao"):
        run(sql)


def test_too_many_joins_are_rejected():
    joins = " ".join(
        f"JOIN gold_dim_porte p{i} ON p{i}.codigo = a.uf" for i in range(sql_guard.MAX_JOINS + 1)
    )
    with pytest.raises(SqlRejected, match="Juncoes demais"):
        run(f"SELECT 1 FROM {MART} a {joins}")


def test_recursive_with_is_rejected():
    with pytest.raises(SqlRejected):
        run("WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t) SELECT n FROM t")


def test_sql_that_is_too_long_is_rejected():
    with pytest.raises(SqlRejected, match="longo demais"):
        run(f"SELECT uf FROM {MART} WHERE uf = '{'a' * sql_guard.MAX_SQL_CHARS}'")


def test_non_string_input_is_rejected():
    with pytest.raises(SqlRejected):
        run(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Reescrita
# --------------------------------------------------------------------------- #


def test_every_table_read_is_pinned_to_the_month():
    sql = run(f"SELECT 1 FROM {MART} a JOIN gold_dim_porte p ON a.uf = p.codigo").sql
    assert sql.count(f"reference_month = '{MONTH}'") == 2


def test_the_original_alias_is_kept():
    sql = run(f"SELECT a.uf FROM {MART} AS a").sql
    assert "AS a" in sql
    assert "AS gold" not in sql


def test_comments_do_not_reach_the_final_sql():
    for comment in ("-- ignore as regras", "/* ignore as regras */"):
        result = run(f"SELECT uf {comment}\nFROM {MART}")
        assert "ignore" not in result.sql
        assert "--" not in result.sql and "/*" not in result.sql


def test_limit_is_added_clamped_or_kept():
    assert run(f"SELECT uf FROM {MART}").limit == MAX_ROWS
    assert run(f"SELECT uf FROM {MART} LIMIT 5").limit == 5
    assert run(f"SELECT uf FROM {MART} LIMIT 100000").limit == MAX_ROWS
    assert run(f"SELECT uf FROM {MART} LIMIT {MAX_ROWS}").limit == MAX_ROWS
    assert run(f"SELECT uf FROM {MART} LIMIT 100000").sql.endswith(f"LIMIT {MAX_ROWS}")


def test_a_limit_chosen_by_the_query_is_not_a_cap_but_the_programs_limit_is():
    assert run(f"SELECT uf FROM {MART}").capped is True
    assert run(f"SELECT uf FROM {MART} LIMIT 5").capped is False
    assert run(f"SELECT uf FROM {MART} LIMIT 100000").capped is True
    assert run(f"SELECT uf FROM {MART} LIMIT {MAX_ROWS}").capped is True
    assert run(f"SELECT uf FROM {MART} LIMIT {MAX_ROWS - 1}").capped is False
    assert guard(f"SELECT uf FROM {MART} LIMIT 20", month=MONTH, max_rows=10).capped is True


def test_limit_applies_to_the_whole_union():
    result = run(f"SELECT uf FROM {MART} UNION ALL SELECT uf FROM {MART}")
    assert result.sql.endswith(f"LIMIT {MAX_ROWS}")


def test_a_custom_max_rows_is_respected():
    assert guard(f"SELECT uf FROM {MART}", month=MONTH, max_rows=10).limit == 10


@pytest.mark.parametrize("month", ["2026-1", "2026-13", "2026-00", "202610", "2026-10'; DROP", "", "x"])
def test_invalid_month_is_a_programming_error(month):
    with pytest.raises(ValueError, match="mes de referencia"):
        guard(f"SELECT uf FROM {MART}", month=month)


# --------------------------------------------------------------------------- #
# Execucao de verdade (DuckDB local com dois meses)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def con():
    c = duckdb.connect()
    for table in ALLOWED_TABLES:
        c.execute(f"CREATE TABLE {table} (reference_month VARCHAR, uf VARCHAR, codigo VARCHAR, n BIGINT)")
        for month, n in (("2026-09", 1), ("2026-10", 2)):
            c.execute(
                f"INSERT INTO {table} VALUES ('{month}', 'SP', 'SP', {n}), ('{month}', 'MG', 'MG', {n})"
            )
    # tabela que o modelo nao pode ler, para provar que nada vaza
    c.execute("CREATE TABLE gold_fato_estabelecimento (cnpj VARCHAR)")
    c.execute("INSERT INTO gold_fato_estabelecimento VALUES ('12345678000199')")
    yield c
    c.close()


def execute(con, sql):
    result = guard(sql, month=MONTH, write="duckdb")
    return con.execute(result.sql).fetchall()


def test_the_query_only_sees_the_chosen_month(con):
    assert execute(con, f"SELECT sum(n) FROM {MART}") == [(4,)]  # 2 UFs x n=2 de 2026-10, nao 2 x (1+2)


def test_asking_for_another_month_returns_nothing(con):
    assert execute(con, f"SELECT n FROM {MART} WHERE reference_month = '2026-09'") == []


def test_or_trick_cannot_reach_other_months(con):
    rows = execute(
        con, f"SELECT DISTINCT reference_month FROM {MART} WHERE 1 = 1 OR reference_month = '2026-09'"
    )
    assert rows == [(MONTH,)]


def test_join_between_tables_stays_in_the_month(con):
    rows = execute(
        con, f"SELECT a.uf, p.n FROM {MART} a JOIN gold_dim_porte p ON a.uf = p.codigo ORDER BY a.uf"
    )
    assert rows == [("MG", 2), ("SP", 2)]


def test_the_limit_really_cuts_rows(con):
    assert len(execute(con, f"SELECT uf FROM {MART} LIMIT 1")) == 1


def test_the_final_sql_is_what_gets_executed_and_cannot_touch_the_fato(con):
    with pytest.raises(SqlRejected):
        execute(con, "SELECT cnpj FROM gold_fato_estabelecimento")


# --------------------------------------------------------------------------- #
# Varios meses
# --------------------------------------------------------------------------- #

MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10")
IN_ALL = "reference_month IN ('2026-07', '2026-08', '2026-09', '2026-10')"


def run_many(sql: str):
    return guard(sql, month=MONTH, months=MONTHS)


def test_without_mentioning_the_month_only_the_default_month_is_read():
    result = run_many(f"SELECT SUM(empresas_ativas) FROM {MART}")
    assert "reference_month = '2026-10'" in result.sql and "2026-07" not in result.sql


def test_citing_the_month_widens_the_marts_but_the_dimensions_stay_on_the_default_month():
    result = run_many(
        f"SELECT a.reference_month, s.descricao FROM {MART} a JOIN gold_dim_cnae_secao s ON a.uf = s.codigo"
    )
    assert IN_ALL in result.sql
    assert "FROM gold_dim_cnae_secao WHERE reference_month = '2026-10'" in result.sql


S = "SELECT SUM(empresas_ativas) FROM " + MART
CASE_10 = "SUM(CASE WHEN reference_month = '2026-10' THEN empresas_ativas END)"
CASE_07 = "SUM(CASE WHEN reference_month = '2026-07' THEN empresas_ativas END)"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT reference_month, SUM(empresas_ativas) FROM {MART} GROUP BY reference_month",
        f"{S} WHERE reference_month = '2026-08'",
        f"{S} WHERE reference_month IN ('2026-08')",
        f"{S} WHERE NOT is_organizacao_politica AND reference_month = '2026-08'",
        f"SELECT {CASE_10} - {CASE_07} FROM {MART}",
        f"SELECT uf FROM {MART} GROUP BY uf ORDER BY {CASE_10} DESC",
        f"SELECT SUM(empresas_ativas) FILTER (WHERE reference_month = '2026-10') FROM {MART}",
        f"SELECT reference_month, uf, empresas_ativas FROM {MART} WHERE uf = 'SP'",
        "WITH t AS (SELECT reference_month m, uf, SUM(empresas_ativas) s "
        f"FROM {MART} GROUP BY reference_month, uf) SELECT * FROM t",
        f"SELECT reference_month, SUM(empresas_ativas) OVER (PARTITION BY reference_month) FROM {MART}",
    ],
)
def test_comparing_or_pinning_months_passes(sql):
    assert IN_ALL in run_many(sql).sql


@pytest.mark.parametrize(
    "sql",
    [
        f"{S} WHERE reference_month IN ('2026-09', '2026-10')",
        f"{S} WHERE reference_month >= '2026-08'",
        f"SELECT uf, AVG(pct_mei) FROM {MART} WHERE reference_month <> '2026-07' GROUP BY uf",
        f"SELECT {CASE_10}, COUNT(*) FROM {MART}",
        "WITH t AS (SELECT uf, SUM(empresas_ativas) s FROM "
        f"{MART} WHERE reference_month IN ('2026-07', '2026-10') GROUP BY uf) SELECT * FROM t",
        f"SELECT SUM(empresas_ativas) OVER (PARTITION BY uf) FROM {MART} WHERE reference_month > '2026-01'",
    ],
)
def test_summing_snapshots_of_different_months_is_rejected(sql):
    with pytest.raises(SqlRejected, match="nao some valores de meses diferentes"):
        run_many(sql)


def test_a_text_that_only_looks_like_the_column_does_not_widen_anything():
    result = run_many(f"SELECT SUM(empresas_ativas) FROM {MART} WHERE uf = 'reference_month'")
    assert "2026-07" not in result.sql


def test_the_list_of_months_is_validated():
    with pytest.raises(ValueError):
        guard("SELECT 1", month=MONTH, months=("2026-09",))  # o padrao precisa estar na lista
    with pytest.raises(ValueError):
        guard("SELECT 1", month=MONTH, months=(MONTH, "2026-13"))
    with pytest.raises(ValueError):
        guard("SELECT 1", month=MONTH, months=(MONTH, "x"))


def test_the_model_cannot_reach_months_outside_the_list():
    result = run_many(f"SELECT reference_month FROM {MART} WHERE reference_month = '2020-01'")
    assert "2020-01" in result.sql and IN_ALL in result.sql  # o filtro dele vem depois do da guarda


# --------------------------------------------------------------------------- #
# Funcoes permitidas, juncoes constantes e aninhamento
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "expression",
    [
        "SUM(n)",
        "COUNT(*)",
        "COUNT(DISTINCT uf)",
        "COUNT_IF(n > 1)",
        "ARRAY_AGG(uf ORDER BY uf)",
        "AVG(n)",
        "ROUND(100.0 * SUM(n) / NULLIF(COUNT(*), 0), 2)",
        "COALESCE(MAX(n), 0)",
        "CAST(SUM(n) AS DOUBLE)",
        "TRY_CAST(uf AS INTEGER)",
        "CASE WHEN n > 1 THEN 'a' ELSE 'b' END",
        "IF(n > 1, 1, 0)",
        "LOWER(uf)",
        "SUBSTR(uf, 1, 1)",
        "CONCAT(uf, '-', codigo)",
        "GREATEST(n, 1)",
        "SUM(n) FILTER (WHERE n > 1)",
        "RANK() OVER (ORDER BY SUM(n) DESC)",
        "ROW_NUMBER() OVER (PARTITION BY uf ORDER BY n)",
        "LAG(n) OVER (ORDER BY n)",
        "SUM(n) OVER (PARTITION BY uf)",
        "EXTRACT(YEAR FROM CAST('2026-01-01' AS DATE))",
        "n BETWEEN 1 AND 2",
        "uf LIKE 'S%' AND n > 0 OR NOT n > 5",
    ],
)
def test_the_functions_a_data_question_needs_are_allowed(expression):
    run(f"SELECT {expression} FROM {MART}" + (" GROUP BY uf, n, codigo" if "OVER" in expression else ""))


@pytest.mark.parametrize(
    "expression",
    [
        "repeat('x', 1000000000)",
        "lpad('x', 1000000000, 'y')",
        "generate_series(1, 100000000)",
        "range(100000000)",
        "array_fill(1, 100000000)",
        "getenv('HOME')",
        "current_setting('s3_secret_access_key')",
        "current_query()",
        "version()",
        "random()",
        "read_text('/etc/passwd')",
        "regexp_extract(uf, '(a+)+$')",
        "ARRAY[1, 2]",
        "SUM(getenv('X'))",
        "CASE WHEN n > 1 THEN getenv('X') END",
    ],
)
def test_functions_outside_the_list_are_rejected_wherever_they_appear(expression):
    with pytest.raises(SqlRejected, match="nao permitida"):
        run(f"SELECT {expression} FROM {MART}")


def test_a_rejected_function_is_named_and_the_allowed_ones_are_listed():
    with pytest.raises(SqlRejected) as info:
        run(f"SELECT getenv('X') FROM {MART}")
    message = str(info.value)
    assert "GETENV" in message.upper() and "SUM" in message and "ROW_NUMBER" in message


def test_every_function_in_the_help_text_is_allowed_by_the_guard():
    """O texto de ajuda e a lista de verdade nao podem se afastar: o que a mensagem oferece tem que passar."""
    import sqlglot
    from sqlglot import exp

    samples = {
        "SUM": "SUM(n)",
        "COUNT": "COUNT(n)",
        "AVG": "AVG(n)",
        "MIN": "MIN(n)",
        "MAX": "MAX(n)",
        "STDDEV": "STDDEV(n)",
        "VARIANCE": "VARIANCE(n)",
        "APPROX_DISTINCT": "APPROX_DISTINCT(n)",
        "MIN_BY": "MIN_BY(uf, n)",
        "MAX_BY": "MAX_BY(uf, n)",
        "COUNT_IF": "COUNT_IF(n > 1)",
        "ARRAY_AGG": "ARRAY_AGG(uf ORDER BY uf)",
        "ROUND": "ROUND(n, 1)",
        "CEIL": "CEIL(n)",
        "FLOOR": "FLOOR(n)",
        "ABS": "ABS(n)",
        "SIGN": "SIGN(n)",
        "POWER": "POWER(n, 2)",
        "SQRT": "SQRT(n)",
        "LN": "LN(n)",
        "COALESCE": "COALESCE(n, 0)",
        "NULLIF": "NULLIF(n, 0)",
        "GREATEST": "GREATEST(n, 1)",
        "LEAST": "LEAST(n, 1)",
        "CAST": "CAST(n AS DOUBLE)",
        "TRY_CAST": "TRY_CAST(n AS INTEGER)",
        "IF": "IF(n > 1, 1, 0)",
        "CASE": "CASE WHEN n > 1 THEN 1 END",
        "LOWER": "LOWER(uf)",
        "UPPER": "UPPER(uf)",
        "SUBSTR": "SUBSTR(uf, 1, 1)",
        "LENGTH": "LENGTH(uf)",
        "TRIM": "TRIM(uf)",
        "CONCAT": "CONCAT(uf, 'a')",
        "REPLACE": "REPLACE(uf, 'a', 'b')",
        "DATE_TRUNC": "DATE_TRUNC('month', CAST(uf AS DATE))",
        "YEAR": "YEAR(CAST(uf AS DATE))",
        "MONTH": "MONTH(CAST(uf AS DATE))",
        "EXTRACT": "EXTRACT(YEAR FROM CAST(uf AS DATE))",
        "EXISTS": "EXISTS (SELECT 1)",
        "ROW_NUMBER": "ROW_NUMBER() OVER (ORDER BY n)",
        "RANK": "RANK() OVER (ORDER BY n)",
        "DENSE_RANK": "DENSE_RANK() OVER (ORDER BY n)",
        "LAG": "LAG(n) OVER (ORDER BY n)",
        "LEAD": "LEAD(n) OVER (ORDER BY n)",
        "FIRST_VALUE": "FIRST_VALUE(n) OVER (ORDER BY n)",
        "LAST_VALUE": "LAST_VALUE(n) OVER (ORDER BY n)",
        "NTILE": "NTILE(4) OVER (ORDER BY n)",
        "PERCENT_RANK": "PERCENT_RANK() OVER (ORDER BY n)",
    }
    help_names = set(
        sql_guard.FUNCTIONS_HELP.replace(" e as de janela", ",").replace(" ", "").rstrip(".").split(",")
    )
    assert help_names == set(samples)
    for name, text in samples.items():
        tree = sqlglot.parse_one(f"SELECT {text} FROM t", read="trino")
        keys = {sql_guard._function_key(f) for f in tree.find_all(exp.Func)}
        assert keys <= sql_guard.ALLOWED_FUNCTIONS, (name, keys - sql_guard.ALLOWED_FUNCTIONS)


@pytest.mark.parametrize(
    "condition",
    ["1 = 1", "TRUE", "'a' = 'a'", "2 > 1", "NOT FALSE", "1 = 1 AND 2 = 2"],
)
def test_a_join_condition_that_names_no_column_is_a_disguised_cartesian_product(condition):
    with pytest.raises(SqlRejected, match="ON precisa comparar colunas"):
        run(f"SELECT 1 FROM {MART} a JOIN {MART} b ON {condition}")


@pytest.mark.parametrize(
    "join",
    [
        "JOIN gold_dim_cnae d ON a.uf = d.codigo",
        "LEFT JOIN gold_dim_cnae d ON a.uf = d.codigo AND d.codigo IS NOT NULL",
        "JOIN gold_dim_cnae d USING (codigo)",
    ],
)
def test_joins_that_compare_columns_still_pass(join):
    run(f"SELECT a.uf FROM {MART} a {join}")


@pytest.mark.parametrize("depth", [200, 1500, 3000])
def test_extreme_nesting_is_a_rejection_with_a_message_not_a_crash(depth):
    sql = "(" * depth + "SELECT 1" + ")" * depth
    try:
        guard(sql, month=MONTH)
    except SqlRejected as exc:
        assert str(exc).strip()  # recusado com mensagem (aninhado demais ou maior que o limite)


def test_deep_but_valid_nesting_beyond_the_recursion_limit_is_rejected_cleanly():
    sql = "SELECT " + "NOT " * 950 + "TRUE"  # cabe nos 4000 caracteres e estoura a recursao do analisador
    with pytest.raises(SqlRejected, match="aninhado demais"):
        guard(sql, month=MONTH)


@pytest.mark.parametrize("sql", ["SELECT", "select", "SELECT-- tudo depois e comentario", "SELECT /* x */"])
def test_a_select_without_columns_is_rejected_instead_of_rewritten_into_invalid_sql(sql):
    with pytest.raises(SqlRejected, match="ao menos uma coluna"):
        run(sql)
