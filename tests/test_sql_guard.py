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
