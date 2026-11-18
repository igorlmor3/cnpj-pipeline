"""Guarda de SQL: valida e reescreve o SQL gerado pelo modelo antes de qualquer execucao.

O que e executado nunca e o texto original: o SQL e lido para uma arvore, validado, reescrito e
gerado de novo. Assim, comentarios e qualquer sintaxe que a arvore nao represente nao passam.

Regras (qualquer violacao levanta SqlRejected, com uma mensagem que o agente pode usar para corrigir):

- exatamente uma instrucao, e ela deve ser um SELECT (com WITH e UNION permitidos);
- so tabelas da lista permitida (marts agregados e dimensoes), sem catalogo e sem outro banco;
  o fato por estabelecimento nao esta na lista, entao nenhuma linha por CNPJ e acessivel;
- cada leitura de tabela e trocada por uma subconsulta fixa nos meses de referencia escolhidos pelo
  programa, nunca pelo modelo: a consulta nao consegue varrer meses fora da lista publicada. Sem
  `months` (ou sem que a consulta cite reference_month), so o mes padrao e lido, o que mantem as
  contas de uma foto unica. Com varios meses e uma consulta que cita reference_month, os marts podem
  ser lidos em todos os meses da lista, e uma verificacao barra somar fotos de meses diferentes (o
  mesmo cadastro contado mais de uma vez); as dimensoes ficam sempre no mes padrao;
- juncoes precisam de condicao (sem produto cartesiano) e ha um maximo de juncoes por consulta;
- o resultado tem limite de linhas, acrescentado ou reduzido para o maximo.

A resolucao de nomes usa o escopo do analisador (nao uma busca textual), para que uma CTE com o
mesmo nome de uma tabela real, definida em outro lugar da consulta, nao esconda a tabela real.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

DEFAULT_DATABASE = "cnpj"
MAX_ROWS = 200
MAX_JOINS = 4
MAX_SQL_CHARS = 4000
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MONTH_COLUMN = "reference_month"
MAX_MONTHS = 36

MARTS = (
    "gold_mart_aberturas_mensal",
    "gold_mart_estabelecimentos_ativos",
    "gold_mart_perfil_porte_natureza",
    "gold_mart_simples_mei_uf",
    "gold_mart_socios_qualificacao",
)
DIMENSIONS = (
    "gold_dim_cnae",
    "gold_dim_cnae_secao",
    "gold_dim_municipio",
    "gold_dim_natureza_juridica",
    "gold_dim_porte",
    "gold_dim_qualificacao_socio",
    "gold_dim_situacao_cadastral",
)
ALLOWED_TABLES = frozenset(MARTS + DIMENSIONS)


# O sqlglot avisa no log quando cai para "Command"; a guarda ja rejeita esses casos com mensagem propria.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class SqlRejected(ValueError):
    """O SQL nao passou na guarda. A mensagem diz o que corrigir."""


@dataclass(frozen=True)
class GuardResult:
    sql: str  # SQL final, que deve ser o unico executado
    tables: tuple[str, ...]  # tabelas lidas, ordenadas
    limit: int  # limite de linhas aplicado
    capped: bool = (
        True  # o limite veio do programa (a consulta nao tinha um menor); False se foi escolha da consulta
    )


def _allowed_list(allowed: frozenset[str]) -> str:
    return ", ".join(sorted(allowed))


def _parse(sql: str) -> exp.Expression:
    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("SQL vazio.")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlRejected(f"SQL longo demais ({len(sql)} caracteres; o maximo e {MAX_SQL_CHARS}).")
    try:
        statements = [s for s in sqlglot.parse(sql, read="trino") if s is not None]
    except SqlglotError as exc:
        raise SqlRejected(f"SQL invalido: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise SqlRejected("Envie exatamente uma instrucao SELECT.")
    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.SetOperation)):
        raise SqlRejected("So consultas SELECT sao permitidas.")
    return statement


def _check_structure(statement: exp.Expression) -> None:
    joins = 0
    for node in statement.walk():
        if isinstance(node, (exp.Command, exp.Into, exp.Lock, exp.Pragma)):
            raise SqlRejected("So consultas SELECT sao permitidas.")
        if isinstance(node, (exp.Unnest, exp.Lateral)):
            # a gold nao tem colunas de lista; UNNEST so serviria para gerar linhas em massa
            raise SqlRejected("UNNEST e LATERAL nao sao permitidos.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SqlRejected("WITH RECURSIVE nao e permitido.")
        if isinstance(node, exp.Join):
            joins += 1
            if not (node.args.get("on") or node.args.get("using")):
                raise SqlRejected(
                    "Toda juncao precisa de condicao ON ou USING; produto cartesiano nao e permitido."
                )
    if joins > MAX_JOINS:
        raise SqlRejected(f"Juncoes demais ({joins}); o maximo e {MAX_JOINS}.")


def _classify_tables(statement: exp.Expression) -> tuple[list[exp.Table], int]:
    """Separa as tabelas reais das referencias a CTE ou subconsulta, usando o escopo."""
    try:
        scopes = traverse_scope(statement)
    except SqlglotError as exc:
        raise SqlRejected(f"Nao foi possivel resolver os nomes da consulta: {exc}") from exc
    real: dict[int, exp.Table] = {}
    derived: set[int] = set()
    for scope in scopes:
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope):
                derived.add(id(table))
            else:
                real[id(table)] = table
    unclassified = [t for t in statement.find_all(exp.Table) if id(t) not in real and id(t) not in derived]
    if unclassified:
        raise SqlRejected("Funcoes de tabela e fontes de dados desse tipo nao sao permitidas.")
    return list(real.values()), len(derived)


def _validate_table(table: exp.Table, allowed: frozenset[str], database: str) -> str:
    if table.args.get("catalog"):
        raise SqlRejected("Nao qualifique tabelas com catalogo.")
    db = table.args.get("db")
    if db is not None and db.name.lower() != database:
        raise SqlRejected(f"So o banco '{database}' pode ser consultado.")
    if not isinstance(table.this, exp.Identifier):
        raise SqlRejected("Funcoes de tabela nao sao permitidas.")
    name = table.name.lower()
    if name not in allowed:
        raise SqlRejected(
            f"Tabela '{table.name}' nao permitida. Tabelas disponiveis: {_allowed_list(allowed)}."
        )
    return name


def _month_subquery(name: str, alias: str, months: tuple[str, ...]) -> exp.Subquery:
    if len(months) == 1:
        condition = f"{MONTH_COLUMN} = '{months[0]}'"
    else:
        condition = f"{MONTH_COLUMN} IN ({', '.join(repr(m) for m in months)})"
    inner = sqlglot.parse_one(f"SELECT * FROM {name} WHERE {condition}", read="trino")
    return exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias)))


def _is_month_column(node: exp.Expression) -> bool:
    return isinstance(node, exp.Column) and node.name.lower() == MONTH_COLUMN


def _mentions_month(node: exp.Expression) -> bool:
    return any(_is_month_column(c) for c in node.find_all(exp.Column))


def _conjuncts(condition: exp.Expression) -> list[exp.Expression]:
    if isinstance(condition, exp.Paren):
        return _conjuncts(condition.this)
    if isinstance(condition, exp.And):
        return _conjuncts(condition.left) + _conjuncts(condition.right)
    return [condition]


def _pins_one_month(where: exp.Where | None) -> bool:
    """O WHERE fixa reference_month em um unico texto (= ou IN com um valor)."""
    if where is None:
        return False
    for part in _conjuncts(where.this):
        if isinstance(part, exp.EQ):
            sides = (part.left, part.right)
            if any(_is_month_column(a) for a in sides) and any(
                isinstance(b, exp.Literal) and b.is_string for b in sides
            ):
                return True
        if isinstance(part, exp.In) and _is_month_column(part.this):
            values = part.expressions
            if len(values) == 1 and isinstance(values[0], exp.Literal) and values[0].is_string:
                return True
    return False


def _sums_across_months(select: exp.Select) -> bool:
    """True se esta consulta agrega linhas de meses diferentes juntas, sem separar por mes.

    Esta tudo bem: agrupar por reference_month, fixar um unico mes no WHERE, nao agregar (listar
    linhas), ou deixar o mes dentro de cada agregado (SUM(CASE WHEN reference_month = ...)).
    """
    group = select.args.get("group")
    if group is not None and _mentions_month(group):
        return False
    if _pins_one_month(select.args.get("where")):
        return False
    aggregates: list[exp.Expression] = []
    for node in select.find_all(exp.AggFunc, exp.Window):
        if node.find_ancestor(exp.Select) is not select:
            continue  # pertence a uma subconsulta, que tem o seu proprio escopo
        if isinstance(node, exp.AggFunc) and node.find_ancestor(exp.Window):
            continue  # o agregado de um Window e conferido junto com ele
        aggregates.append(node.parent if isinstance(node.parent, exp.Filter) else node)
    return not all(_mentions_month(a) for a in aggregates)


def _check_month_use(statement: exp.Expression, marts: frozenset[str]) -> None:
    """Barra a consulta que soma, no mesmo grupo, fotos de meses diferentes do mesmo cadastro."""
    for scope in traverse_scope(statement):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        reads_mart = any(
            not isinstance(scope.sources.get(t.alias_or_name), Scope) and t.name.lower() in marts
            for t in scope.tables
        )
        if reads_mart and _sums_across_months(select):
            raise SqlRejected(
                "Com mais de um mes na consulta, nao some valores de meses diferentes: cada mes e uma "
                "foto do mesmo cadastro e a soma conta tudo mais de uma vez. Fixe um mes com "
                "reference_month = 'AAAA-MM', agrupe por reference_month ou use SUM(CASE WHEN "
                "reference_month = 'AAAA-MM' THEN coluna END) para cada mes."
            )


def _apply_limit(statement: exp.Expression, max_rows: int) -> tuple[int, bool]:
    """Devolve o limite aplicado e se ele foi imposto pelo programa (e nao escolhido pela consulta)."""
    current = statement.args.get("limit")
    wanted = max_rows
    capped = True
    if current is not None:
        value = current.expression
        if isinstance(value, exp.Literal) and not value.is_string and value.name.isdigit():
            wanted = min(int(value.name), max_rows)
            capped = int(value.name) >= max_rows
    statement.set("limit", exp.Limit(expression=exp.Literal.number(wanted)))
    return wanted, capped


def guard(
    sql: str,
    *,
    month: str,
    months: tuple[str, ...] | None = None,
    allowed_tables: frozenset[str] = ALLOWED_TABLES,
    max_rows: int = MAX_ROWS,
    database: str = DEFAULT_DATABASE,
    write: str = "trino",
) -> GuardResult:
    """Valida e reescreve o SQL. Os meses vem do programa; o modelo nao escolhe o que varrer.

    `month` e o mes padrao (o mais recente). `months` e a lista de meses publicados; sem ela, so
    `month` pode ser lido."""
    if not MONTH_RE.match(month):
        raise ValueError(f"mes de referencia invalido: {month!r}")
    available = tuple(sorted(set(months))) if months else (month,)
    if len(available) > MAX_MONTHS or not all(MONTH_RE.match(m) for m in available):
        raise ValueError("lista de meses invalida")
    if month not in available:
        raise ValueError("o mes padrao precisa estar na lista de meses")
    allowed = frozenset(t.lower() for t in allowed_tables)

    statement = _parse(sql)
    _check_structure(statement)
    real, _ = _classify_tables(statement)
    names = {id(t): _validate_table(t, allowed, database) for t in real}

    multi = len(available) > 1 and _mentions_month(statement)
    if multi:
        _check_month_use(statement, frozenset(MARTS))
    for table in real:
        alias = table.alias or table.name
        wide = multi and names[id(table)] in MARTS
        table.replace(_month_subquery(names[id(table)], alias, available if wide else (month,)))

    limit, capped = _apply_limit(statement, max_rows)
    final = statement.sql(dialect=write, comments=False)
    return GuardResult(sql=final, tables=tuple(sorted(set(names.values()))), limit=limit, capped=capped)
