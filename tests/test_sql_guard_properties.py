"""Testes de propriedade da guarda de SQL.

Os testes de exemplo (test_sql_guard.py) cobrem o que alguem pensou em atacar. Aqui o `hypothesis`
gera consultas e entradas quebradas que ninguem escreveu, e a guarda tem que sustentar quatro
propriedades para qualquer uma delas:

1. Nunca quebra: ou levanta SqlRejected, ou devolve um GuardResult. Nenhuma outra excecao.
2. Pos-condicoes do que aceita: uma unica instrucao SELECT, sem comentarios, so tabelas permitidas,
   cada leitura fixada nos meses da lista publicada (as dimensoes, so no mes padrao), limite de
   linhas dentro do maximo, e `tables` diz a verdade sobre o que a consulta le.
3. Metamorfica: o resultado de qualquer consulta aceita nao muda se os dados que o modelo nao pode
   ler (outros meses, tabelas fora da lista, o fato por CNPJ, o catalogo) forem removidos do banco.
4. O limite de linhas vale para o que o motor de fato devolve.

A propriedade 3 nao depende do codigo da guarda: compara duas execucoes no DuckDB, uma com tudo
no banco e outra so com o que e permitido ler.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache

import duckdb
import pytest
import sqlglot
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from cnpj_pipeline.agent.sql_guard import (
    ALLOWED_TABLES,
    MARTS,
    MAX_SQL_CHARS,
    GuardResult,
    SqlRejected,
    guard,
)

DEFAULT_MONTH = "2026-10"
WINDOW = ("2026-08", "2026-09", "2026-10")
DATA_MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10", "2026-11")
OUTSIDE = ("2026-07", "2026-11", "2025-01", "2099-12")
SECRET = "SEGREDO"

EXAMPLES = int(os.environ.get("GUARD_PROPERTY_EXAMPLES", "300"))
PROPERTY = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)

# --------------------------------------------------------------------------- #
# Geradores
# --------------------------------------------------------------------------- #

REAL = sorted(ALLOWED_TABLES)
FORBIDDEN = [
    "gold_fato_estabelecimento",
    "silver_estabelecimentos",
    "information_schema.tables",
    "information_schema.columns",
    "cnpj.gold_fato_estabelecimento",
    "other.cnpj.gold_mart_simples_mei_uf",
    "main.gold_mart_simples_mei_uf",
    "cnpj.gold_mart_simples_mei_uf",
    "a",
    "t",
    "x",
]
# nomes que uma CTE pode tomar, inclusive os de tabelas reais e proibidas
CTE_NAMES = ["c", "a", "gold_mart_simples_mei_uf", "gold_dim_cnae", "gold_fato_estabelecimento"]
COLUMNS = ["*", "uf", "n", "uf, n", "codigo, uf", "reference_month, uf", "t.*", "uf, SUM(n)", "COUNT(*)"]
AGGREGATES = [
    "COUNT(*)",
    "SUM(n)",
    "MAX(reference_month)",
    "COUNT(DISTINCT uf)",
    "SUM(CASE WHEN reference_month = '2026-10' THEN n END)",
    "SUM(CASE WHEN reference_month = '2026-07' THEN n END)",
    "SUM(n) OVER (PARTITION BY uf)",
    "SUM(n) FILTER (WHERE reference_month = '2026-09')",
]


@st.composite
def table_ref(draw) -> str:
    pool = REAL * 3 + FORBIDDEN
    name = draw(st.sampled_from(pool))
    style = draw(st.sampled_from(["plain", "plain", "quoted", "upper", "mixed"]))
    parts = name.split(".")
    if style == "quoted":
        parts = [f'"{p}"' for p in parts]
    elif style == "upper":
        parts = [p.upper() for p in parts]
    elif style == "mixed":
        parts = [p.title() for p in parts]
    return ".".join(parts)


months = st.sampled_from(WINDOW + OUTSIDE)
month_predicates = st.one_of(
    months.map(lambda m: f"reference_month = '{m}'"),
    months.map(lambda m: f"t.reference_month = '{m}'"),
    months.map(lambda m: f"reference_month <> '{m}'"),
    months.map(lambda m: f"reference_month >= '{m}'"),
    months.map(lambda m: f"'{m}' = reference_month"),
    st.lists(months, min_size=1, max_size=4).map(
        lambda ms: "reference_month IN (" + ", ".join(f"'{m}'" for m in ms) + ")"
    ),
    st.sampled_from(
        [
            "reference_month LIKE '2026-%'",
            "reference_month IS NOT NULL",
            "1 = 1 OR reference_month = '2026-10'",
            "reference_month = '2026-10' OR n > 0",
            "NOT reference_month = '2026-10'",
            "CAST(reference_month AS VARCHAR) <> ''",
        ]
    ),
)
plain_predicates = st.sampled_from(
    ["uf = 'SP'", "n > 0", "codigo IS NOT NULL", "1 = 1", "n BETWEEN 0 AND 100"]
)
leaf_predicates = st.one_of(month_predicates, plain_predicates)
predicates = st.recursive(
    leaf_predicates,
    lambda inner: st.one_of(
        st.builds(lambda a, b, op: f"({a}) {op} ({b})", inner, inner, st.sampled_from(["AND", "OR"])),
        st.builds(lambda a: f"NOT ({a})", inner),
    ),
    max_leaves=4,
)
limits = st.sampled_from(
    [
        "",
        "",
        " LIMIT 1",
        " LIMIT 3",
        " LIMIT 5000",
        " LIMIT 99999999999",
        " LIMIT ALL",
        " LIMIT 1 OFFSET 1",
        " OFFSET 1",
        " LIMIT 0",
        " FETCH FIRST 3 ROWS ONLY",
        " LIMIT (SELECT 3)",
        " LIMIT 1e3",
        " LIMIT -1",
        " LIMIT '5'",
    ]
)


@st.composite
def simple_select(draw) -> str:
    columns = draw(st.sampled_from(COLUMNS))
    if draw(st.booleans()):
        columns += ", " + draw(st.sampled_from(AGGREGATES))
    sql = f"SELECT {columns} FROM {draw(table_ref())}"
    if draw(st.booleans()):
        sql += draw(st.sampled_from([" t", " AS t", " b"]))
    if draw(st.booleans()):
        sql += " WHERE " + draw(predicates)
    group = draw(st.sampled_from(["", "", " GROUP BY uf", " GROUP BY reference_month", " GROUP BY 1"]))
    sql += group
    if draw(st.booleans()):
        sql += " ORDER BY 1"
    return sql + draw(limits)


def _join(a: str, b: str, kind: str, cond: str) -> str:
    on = f" ON {cond}" if cond else ""
    return f"SELECT * FROM ({a}) AS l {kind} JOIN ({b}) AS r{on}"


select_statements = st.recursive(
    simple_select(),
    lambda inner: st.one_of(
        st.builds(
            lambda a, b, op: f"({a}) {op} ({b})",
            inner,
            inner,
            st.sampled_from(["UNION", "UNION ALL", "INTERSECT", "EXCEPT"]),
        ),
        st.builds(
            lambda a, b, name: f"WITH {name} AS ({a}) {b}",
            inner,
            simple_select(),
            st.sampled_from(CTE_NAMES),
        ),
        st.builds(
            lambda a, b, name: f"WITH {name} AS ({a}) SELECT * FROM {name} UNION ALL ({b})",
            inner,
            inner,
            st.sampled_from(CTE_NAMES),
        ),
        st.builds(lambda a: f"SELECT * FROM ({a}) AS s", inner),
        st.builds(lambda a: f"SELECT uf, n FROM ({a}) AS s WHERE n > 0", inner),
        st.builds(
            _join,
            inner,
            inner,
            st.sampled_from(["INNER", "LEFT", "CROSS", "FULL"]),
            st.sampled_from(["", "l.uf = r.uf", "1 = 1", "TRUE"]),
        ),
        st.builds(
            lambda a, b: f"{b} AND uf IN ({a})", inner, st.just("SELECT uf FROM gold_dim_cnae WHERE 1=1")
        ),
        st.builds(lambda a: f"SELECT ({a}) AS v", inner),
        st.builds(lambda a: f"SELECT * FROM gold_dim_cnae a JOIN ({a}) b ON a.uf = b.uf", inner),
    ),
    max_leaves=4,
)

COMMENTS = [
    "/* x */",
    "-- x\n",
    "/* ; DROP TABLE t */",
    "/*! SELECT 1 */",
    "/*+ hint */",
    "-- ; SELECT * FROM gold_fato_estabelecimento\n",
    "/* /* aninhado */ */",
]
TAILS = ["", ";", "; SELECT 1", "; DROP TABLE t", " -- fim", " /* fim */", ";;", "; -- x"]


@st.composite
def decorated(draw, base=select_statements) -> str:
    sql = draw(base)
    tokens = sql.split(" ")
    for index, comment in draw(
        st.lists(st.tuples(st.integers(0, max(len(tokens) - 1, 0)), st.sampled_from(COMMENTS)), max_size=3)
    ):
        tokens[index] = comment + tokens[index]
    sql = " ".join(tokens)
    if draw(st.booleans()):
        sql = draw(st.sampled_from([str.upper, str.lower, lambda s: s]))(sql)
    return sql + draw(st.sampled_from(TAILS))


JUNK = [
    "'",
    '"',
    ";",
    "(",
    ")",
    "--",
    "/*",
    "*/",
    "\\",
    "`",
    "\x00",
    "$$",
    "​",
    "ｔ",
    "%",
    "@",
    "$path",
    "'''",
]


@st.composite
def mutated(draw) -> str:
    sql = draw(decorated())
    for _ in range(draw(st.integers(1, 3))):
        position = draw(st.integers(0, len(sql)))
        if draw(st.booleans()) and sql:
            sql = sql[:position] + sql[position + draw(st.integers(1, 8)) :]
        else:
            sql = sql[:position] + draw(st.sampled_from(JUNK)) + sql[position:]
    return sql


# --------------------------------------------------------------------------- #
# Pos-condicoes do que a guarda aceita
# --------------------------------------------------------------------------- #

FORBIDDEN_NODES = (
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Pragma,
    exp.Unnest,
    exp.Lateral,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Merge,
    exp.Copy,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


def _pinned_months(table: exp.Table) -> tuple[str, set[str]]:
    """Confere que a leitura real e `(SELECT * FROM <tabela> WHERE reference_month ...)` e devolve
    (nome, meses fixados)."""
    assert not table.args.get("catalog") and not table.args.get("db"), table.sql()
    assert isinstance(table.this, exp.Identifier), table.sql()
    select = table.parent.parent if isinstance(table.parent, exp.From) else None
    assert isinstance(select, exp.Select), f"leitura fora do padrao: {table.sql()}"
    assert [type(e) for e in select.expressions] == [exp.Star], select.sql()
    assert not select.args.get("joins") and not select.args.get("group"), select.sql()
    assert isinstance(select.parent, exp.Subquery), select.sql()
    where = select.args.get("where")
    assert where is not None, select.sql()
    condition = where.this
    if isinstance(condition, exp.EQ):
        assert condition.this.name == "reference_month" and condition.expression.is_string
        return table.name, {condition.expression.name}
    assert isinstance(condition, exp.In) and condition.this.name == "reference_month", condition.sql()
    assert all(v.is_string for v in condition.expressions), condition.sql()
    return table.name, {v.name for v in condition.expressions}


def assert_postconditions(
    result: GuardResult,
    *,
    month: str,
    months: tuple[str, ...] | None,
    max_rows: int,
    write: str = "trino",
) -> None:
    available = set(months) if months else {month}
    statements = [s for s in sqlglot.parse(result.sql, read=write) if s is not None]
    assert len(statements) == 1, result.sql
    root = statements[0]
    assert isinstance(root, exp.Select | exp.SetOperation), result.sql
    assert not any(isinstance(n, FORBIDDEN_NODES) for n in root.walk()), result.sql
    assert not any(n.comments for n in root.walk()), result.sql

    read: set[str] = set()
    for scope in traverse_scope(root):
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), Scope):
                continue  # CTE ou subconsulta: nao le tabela do banco
            name, pinned = _pinned_months(table)
            assert name in ALLOWED_TABLES, result.sql
            assert pinned <= available, (pinned, available, result.sql)
            if name in MARTS and months and len(months) > 1:
                assert pinned in ({month}, available), result.sql
            else:
                assert pinned == {month}, (name, pinned, result.sql)
            read.add(name)
    # fora das leituras fixadas, nenhuma tabela real aparece (so nomes de CTE)
    cte_names = {c.alias_or_name for c in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        if table.parent is not None and isinstance(table.parent.parent, exp.Select):
            select = table.parent.parent
            if isinstance(select.parent, exp.Subquery) and select.args.get("where") is not None:
                if table.name in ALLOWED_TABLES and table.name not in cte_names:
                    continue
        if table.name not in ALLOWED_TABLES or table.name in cte_names:
            continue
        pytest.fail(f"tabela real fora de uma leitura fixada: {table.sql()} em {result.sql}")
    assert set(result.tables) >= read - cte_names or not read, (result.tables, read)

    limit = root.args.get("limit")
    assert isinstance(limit, exp.Limit), result.sql
    value = limit.expression
    assert isinstance(value, exp.Literal) and not value.is_string, result.sql
    assert 0 <= int(value.name) <= max_rows, result.sql
    assert result.limit == int(value.name) <= max_rows


def try_guard(sql: str, **kwargs) -> GuardResult | None:
    """None se a guarda recusou. Qualquer outra excecao e falha do teste."""
    try:
        return guard(sql, **kwargs)
    except SqlRejected as exc:
        assert str(exc).strip(), "a recusa precisa ter uma mensagem"
        return None


windows = st.sampled_from([None, WINDOW, WINDOW[1:], (DEFAULT_MONTH,)])


# --------------------------------------------------------------------------- #
# 1. Nunca quebra
# --------------------------------------------------------------------------- #


@PROPERTY
@given(sql=st.text(max_size=400))
@example(sql="SELECT")  # achado: virava `SELECT LIMIT 200`, que nao e SQL
@example(sql="SELECT-- tudo depois e comentario")
@example(sql="(" * 1500 + "SELECT 1" + ")" * 1500)  # achado: RecursionError escapava da guarda
@example(sql="SELECT " + "NOT " * 950 + "TRUE")
def test_arbitrary_text_is_rejected_cleanly_or_accepted_safely(sql):
    result = try_guard(sql, month=DEFAULT_MONTH, months=WINDOW)
    if result is not None:
        assert_postconditions(result, month=DEFAULT_MONTH, months=WINDOW, max_rows=200)


@PROPERTY
@given(sql=st.from_regex(r"[A-Za-z0-9_ ,.*()=<>'\";/\-\n+%$|:]{0,300}", fullmatch=True))
def test_sql_shaped_noise_never_crashes(sql):
    result = try_guard(sql, month=DEFAULT_MONTH, months=WINDOW)
    if result is not None:
        assert_postconditions(result, month=DEFAULT_MONTH, months=WINDOW, max_rows=200)


@PROPERTY
@given(sql=mutated(), months_=windows)
def test_broken_variants_of_real_queries_never_crash(sql, months_):
    result = try_guard(sql, month=DEFAULT_MONTH, months=months_)
    if result is not None:
        assert_postconditions(result, month=DEFAULT_MONTH, months=months_, max_rows=200)


# --------------------------------------------------------------------------- #
# 2. Pos-condicoes
# --------------------------------------------------------------------------- #


@PROPERTY
@given(sql=decorated(), months_=windows, max_rows=st.sampled_from([1, 3, 200]))
def test_whatever_the_guard_accepts_satisfies_the_postconditions(sql, months_, max_rows):
    result = try_guard(sql, month=DEFAULT_MONTH, months=months_, max_rows=max_rows)
    if result is not None:
        assert_postconditions(result, month=DEFAULT_MONTH, months=months_, max_rows=max_rows)


@PROPERTY
@given(sql=decorated(), months_=windows)
def test_the_guard_is_deterministic(sql, months_):
    first = try_guard(sql, month=DEFAULT_MONTH, months=months_)
    second = try_guard(sql, month=DEFAULT_MONTH, months=months_)
    assert (first.sql if first else None) == (second.sql if second else None)


@PROPERTY
@given(sql=decorated())
def test_the_output_of_the_guard_passes_the_guard_again_without_widening(sql):
    first = try_guard(sql, month=DEFAULT_MONTH, months=WINDOW)
    if first is None:
        return
    second = try_guard(first.sql, month=DEFAULT_MONTH, months=WINDOW)
    # o que a guarda gerou ou e aceito de novo ou recusado; se aceito, continua dentro das regras
    if second is not None:
        assert_postconditions(second, month=DEFAULT_MONTH, months=WINDOW, max_rows=200)


# --------------------------------------------------------------------------- #
# 3 e 4. Execucao de verdade: o resultado so depende do que e permitido ler
# --------------------------------------------------------------------------- #


def _build(full: bool) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=1")
    months = DATA_MONTHS if full else tuple(m for m in DATA_MONTHS if m in WINDOW)
    for table in sorted(ALLOWED_TABLES):
        con.execute(f"CREATE TABLE {table} (reference_month VARCHAR, uf VARCHAR, codigo VARCHAR, n BIGINT)")
        for month in months:
            index = DATA_MONTHS.index(month)  # o valor depende do mes, nao da posicao no banco
            con.execute(
                f"INSERT INTO {table} VALUES ('{month}', 'SP', 'c{index}', {index + 1}), "
                f"('{month}', 'MG', 'c{index}', {index + 10})"
            )
    if full:
        for secret_table in ("gold_fato_estabelecimento", "silver_estabelecimentos", "a", "t", "x"):
            con.execute(
                f"CREATE TABLE {secret_table} "
                "(reference_month VARCHAR, uf VARCHAR, codigo VARCHAR, n BIGINT, cnpj VARCHAR)"
            )
            con.execute(f"INSERT INTO {secret_table} VALUES ('2026-10', 'SP', '{SECRET}', 999, '{SECRET}')")
    return con


@lru_cache(maxsize=1)
def databases() -> tuple[duckdb.DuckDBPyConnection, duckdb.DuckDBPyConnection]:
    return _build(full=True), _build(full=False)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, max_rows: int):
    try:
        cursor = con.execute(sql)
        return cursor.fetchmany(max_rows), None
    except duckdb.Error as exc:
        return None, str(exc)


@PROPERTY
@given(sql=decorated(), months_=windows, max_rows=st.sampled_from([1, 2, 3, 200]))
def test_the_result_does_not_depend_on_data_the_model_may_not_read(sql, months_, max_rows):
    result = try_guard(sql, month=DEFAULT_MONTH, months=months_, max_rows=max_rows, write="duckdb")
    if result is None:
        return
    full, restricted = databases()
    rows_full, error_full = _rows(full, result.sql, max_rows)
    if rows_full is None:
        return  # erro do motor (coluna que nao existe etc.) nao e o que se testa aqui
    assert len(rows_full) <= max_rows  # propriedade 4: o limite vale para o que o motor devolve
    assert all(SECRET not in map(str, row) for row in rows_full), result.sql
    rows_restricted, error_restricted = _rows(restricted, result.sql, max_rows)
    assert rows_restricted is not None, f"so funciona com dados proibidos: {result.sql} -> {error_restricted}"
    assert sorted(map(repr, rows_full)) == sorted(map(repr, rows_restricted)), result.sql


# --------------------------------------------------------------------------- #
# Entradas patologicas conhecidas (tempo e profundidade)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        "(" * 1500 + "SELECT 1" + ")" * 1500,
        "SELECT " + "(" * 1900 + "1" + ")" * 1900,
        "SELECT 1" + " UNION ALL SELECT 1" * 200,
        "SELECT " + " + ".join(["1"] * 1500),
        "SELECT " + "NOT " * 1500 + "TRUE",
        "SELECT * FROM gold_dim_cnae WHERE " + " AND ".join(["n > 0"] * 400),
        "SELECT '" + "a" * (MAX_SQL_CHARS - 20) + "'",
        "/*" * 1500,
        "'" * 3999,
    ],
)
def test_pathological_inputs_are_handled_within_a_second(sql):
    started = time.monotonic()
    result = try_guard(sql[:MAX_SQL_CHARS], month=DEFAULT_MONTH, months=WINDOW)
    assert time.monotonic() - started < 1.5
    if result is not None:
        assert_postconditions(result, month=DEFAULT_MONTH, months=WINDOW, max_rows=200)
